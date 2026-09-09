"""One supervised candidate; persistent processes and a common sampling clock."""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
import traceback
from pathlib import Path

from .adapters import prepare
from .common import COUNT_KEYS, read, stream_seed, summarize, validate_counts, write


def child(connection, request, config, index, factory):
    try:
        tool, cpus = request["case"]["tool"], request["cpus"]
        native = tool in {"clifft", "symft"}
        affinity = cpus if native else [cpus[index]]
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(0, affinity)
            if sorted(os.sched_getaffinity(0)) != affinity:
                raise ValueError("worker affinity was not retained")
        elif not request["development"]:
            raise ValueError("publication collection requires Linux CPU affinity")
        # Nice workers yield to SSH/monitoring, without reserving an idle core.
        os.nice(5)
        seed = (
            (stream_seed(request["seed"]) + index) % 2**32
            if tool == "tsim"
            else stream_seed(request["seed"], index)
        )
        prepared = factory(
            tool,
            request["case"]["workload"],
            config,
            request["options"]["workers"] if native else 1,
            seed,
        )
        connection.send(
            {
                "status": "ready",
                "metadata": prepared.metadata,
                "affinity": affinity if hasattr(os, "sched_getaffinity") else None,
                "pid": os.getpid(),
                "seed": seed,
            }
        )
        while True:
            instruction = connection.recv()
            if instruction["command"] == "stop":
                break
            prepared.begin(stream_seed(seed, instruction["repetition"]))
            connection.send({"status": "armed"})
            go = connection.recv()
            start = go["start"]
            time.sleep(max(0, start - time.perf_counter()))
            actual_start = time.perf_counter()
            counts = dict.fromkeys(COUNT_KEYS, 0)
            calls, call_seconds = 0, 0.0
            while calls == 0 or time.perf_counter() < start + go["seconds"]:
                before = time.perf_counter()
                result = prepared.sample(instruction["shots"])
                call_seconds += time.perf_counter() - before
                for k in COUNT_KEYS:
                    counts[k] += result[k]
                calls += 1
            end = time.perf_counter()
            validate_counts(counts)
            if counts["attempted_shots"] != calls * instruction["shots"]:
                raise ValueError("sampler did not return the requested attempted-shot count")
            connection.send(
                {
                    "status": "sample",
                    **counts,
                    "api_calls": calls,
                    "call_seconds": call_seconds,
                    "started": actual_start,
                    "ended": end,
                }
            )
    except BaseException:
        connection.send({"status": "error", "error": traceback.format_exc()})
    finally:
        connection.close()


class Pool:
    def __init__(self, request, config, factory=prepare):
        self.processes, self.connections, self.metadata = [], [], []
        ctx = mp.get_context("spawn")
        n = 1 if request["case"]["tool"] in {"clifft", "symft"} else request["options"]["workers"]
        try:
            for index in range(n):
                parent, remote = ctx.Pipe()
                process = ctx.Process(target=child, args=(remote, request, config, index, factory))
                process.start()
                remote.close()
                self.processes.append(process)
                self.connections.append(parent)
            self.metadata = [self.receive(c, "ready") for c in self.connections]
        except BaseException:
            self.close()
            raise

    @staticmethod
    def receive(connection, status):
        result = connection.recv()
        if result["status"] != status:
            raise RuntimeError(result.get("error", "unexpected worker response"))
        return result

    def measure(self, shots, seconds, repetition):
        for c in self.connections:
            c.send({"command": "arm", "shots": shots, "repetition": repetition})
        for c in self.connections:
            self.receive(c, "armed")
        start = time.perf_counter() + 0.02
        for c in self.connections:
            c.send({"command": "go", "start": start, "seconds": seconds})
        samples = [self.receive(c, "sample") for c in self.connections]
        counts = {k: sum(s[k] for s in samples) for k in COUNT_KEYS}
        # Include final IPC and merging. Never sum per-process elapsed times.
        duration = time.perf_counter() - start
        validate_counts(counts)
        return {
            **counts,
            "duration_seconds": duration,
            "api_calls": sum(s["api_calls"] for s in samples),
            "shots_per_call": shots,
            "scheduled_start": start,
            "workers": samples,
            "throughput_attempted_shots_per_second": counts["attempted_shots"] / duration,
        }

    def close(self):
        for c in self.connections:
            try:
                c.send({"command": "stop"})
            except (OSError, BrokenPipeError):
                pass
        for p in self.processes:
            p.join(timeout=0.1)
            if p.is_alive():
                p.kill()
        for p in self.processes:
            p.join(timeout=1)
        for c in self.connections:
            c.close()


def run(request, directory):
    options, case = request["options"], request["case"]
    config = dict(request["config"])
    result = {
        "status": "running",
        "fingerprint": request["fingerprint"],
        "case_id": case["id"],
        "config": config,
        "samples": [],
        "warmup": [],
    }
    checkpoint = directory / "checkpoint.json"

    def phase(name, seconds):
        result.update(phase=name, deadline=time.monotonic() + seconds)
        write(checkpoint, result)

    pool = None
    try:
        phase("prepare", options["setup_timeout_seconds"])
        pool = Pool(request, config)
        result["runtime"] = pool.metadata
        # Calibrate public-call duration as well as lane/chunk capacity. The
        # scalar high-width cases must provide enough shots for every native thread.
        for index in range(6 if request["kind"] == "probe" and case["tool"] != "tsim" else 1):
            phase("warmup", options["call_timeout_seconds"])
            sample = pool.measure(config["shots_per_call"], 0, f"warmup-{index}")
            result["warmup"].append(sample)
            if request["kind"] == "final" or case["tool"] == "tsim":
                break
            duration = max(s["call_seconds"] / s["api_calls"] for s in sample["workers"])
            if 0.25 <= duration <= 2 or index == 5:
                break
            native = case["tool"] in {"clifft", "symft"}
            factor = options["workers"] if native else 1
            minimum = factor * 4 * max(1, config["batch_size"], config["sample_chunk_shots"])
            old = config["shots_per_call"]
            new = max(minimum, min(factor * 1048576, int(old * 0.5 / max(duration, 1e-9))))
            # Whole chunks avoid unfairly starving the final native worker.
            new = max(minimum, (new // minimum) * minimum)
            config["shots_per_call"] = new
            if new == old:
                break
        result["config"] = config
        repetitions = (
            options["probe_repetitions"] if request["kind"] == "probe" else options["repetitions"]
        )
        seconds = (
            options["probe_seconds"] if request["kind"] == "probe" else options["sample_seconds"]
        )
        for index in range(repetitions):
            phase(
                "probe" if request["kind"] == "probe" else "sample",
                seconds + options["call_timeout_seconds"],
            )
            result["samples"].append(pool.measure(config["shots_per_call"], seconds, index))
        result.update(status="success", summary=summarize(result["samples"]))
    except Exception:
        result.update(status="error", error=traceback.format_exc())
    finally:
        if pool:
            pool.close()
    result.pop("deadline", None)
    write(directory / "worker-result.json", result)
    return result


if __name__ == "__main__":
    path = Path(sys.argv[1])
    run(read(path), path.parent)
