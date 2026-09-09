# Host provenance

The operator confirmed these host details on September 9, 2026, after collection:

- Provider: Lambda Cloud
- Instance type: `gpu_1x_h100_sxm5`
- Region: `us-southeast-1`

The automatically collected hardware metadata records one NVIDIA H100 80GB HBM3,
an Intel Xeon Platinum 8480+ host CPU, and 26 logical CPUs available to the run.

The collection command retained the README placeholder
`PROVIDER / INSTANCE TYPE / REGION` in `metadata.json`'s `host_label`. This note
supplies the missing operator-reported description. The original metadata and
execution fingerprint are preserved because `host_label` is included in the
fingerprint; the cloud details above were not verified automatically by the
collector.
