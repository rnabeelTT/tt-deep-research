# Blackhole vs. Blackwell vs. MI350: Architectures, Memory, and Software for LLM Inference

This report compares three AI accelerators for large-language-model (LLM) inference: **NVIDIA Blackwell** (the B200 / GB200 / NVL72 family), **AMD Instinct MI350** (the MI300X accelerator and its MI350 companion), and **Tenstorrent Blackhole** (the p-series accelerator and its Galaxy server). It is organized around four comparison dimensions — compute architecture, chiplet/die design and interconnect, memory systems, and software stack — and then synthesizes how each accelerator's design shapes the two opposing phases of LLM inference: **prefill** (compute-bound) and **decode** (memory-bandwidth-bound).

## A note on terminology and product identity

Three naming subtleties materially affect this comparison and are flagged up front so the reader does not conflate the products.

- **AMD "MI350" vs. the accelerator.** The MI350 is the *companion* chip; the accelerator it pairs with is the **MI300X** (CDNA3) and its successor the **MI350X** (CDNA4). MI300X and MI350X are discrete GPU-only accelerators; they require a companion dual-socket AMD EPYC 9004/9005-series (Zen 4 / Zen 5) CPU over PCIe. The only fully integrated CPU+GPU "APU" in the family is the **MI300A**, which fuses Zen 4 cores with CDNA3 compute dies and sacrifices roughly 25% of accelerator compute to fit the CPU dies in the same package [7][8][9]. Where this report cites accelerator specs (HBM, matrix cores, CUs), it means the MI300X/MI350X, not the MI350 companion chip.
- **Tenstorrent "Blackhole" is a p-series part, not an "N" part.** Tenstorrent's official nomenclature is *Wormhole* n150/n300 and *Blackhole* p100/p150/p300. There is no official "N300" or "N350" Tenstorrent SKU — "N350" is in fact an Intel Core 3 processor part. This report therefore treats "Blackhole" as the p-series Blackhole accelerator (p150/p300) [12].
- **"TenDLa" is not a Tenstorrent product.** The training framework the plan references does not exist under that name; Tenstorrent's training stack is **TT-Blacksmith**, and its inference stack is **TT-Metalium / TT-NN / TT-XLA** [14].

## Comparison table

| Dimension | NVIDIA Blackwell (B200 / GB200 / NVL72) | AMD Instinct MI300X / MI350X | Tenstorrent Blackhole (p150/p300) |
|---|---|---|---|
| Architecture generation | Blackwell (2nd gen) | CDNA3 (MI300X) / CDNA4 (MI350X) | Blackhole (p-series) |
| Compute units | 160 SMs; 640 5th-gen Tensor Cores (4/SM) | MI300X: 8 XCDs, 304 CUs; MI350X: 8 XCDs, 256 CUs | 120 Tensix cores |
| Accelerated cores | 5th-gen Tensor Cores + 2nd-gen Transformer Engine | Matrix Cores (CDNA3/4); MXFP4/MXFP6 support | Block FP8 tensor cores |
| RISC-V / host cores | Grace CPU (ARM) companion | EPYC 9004/9005 (Zen 4/5) companion | 16 SiFive x280 Big RISC-V cores |
| Process node | TSMC 4NP | XCD: TSMC N5 (MI300X) / N3P (MI350X); IOD: N6 | TSMC 6nm |
| Chiplet/die topology | 2 reticle-limited dies (first chiplet GPU) | 8 XCD + 4 IODs (MI300X) / 2 IODs (MI350X) | Chiplet-based (single package) |
| Total transistors | 208 billion | ~191 billion | Not published |
| Peak AI performance | Up to ~98.9 PFLOPS (FP8) | MI300X ~2.6 PFLOPS (FP8) | 664 TFLOPS (Block FP8) |
| Interconnect | NVLink-C2C + NVLink Switch; NV-HBI chip-to-chip | Infinity Fabric (~4 TB/s) | TT-LINK (chip-to-chip) |
| HBM generation | HBM3e | HBM3 (MI300X) / HBM3e (MI350X) | GDDR6 (not HBM) |
| HBM capacity | 192 GB | 192 GB (MI300X) / 288 GB (MI350X) | Up to 32 GB |
| HBM bandwidth | Up to 8 TB/s | 5.3 TB/s (MI300X) / 8 TB/s (MI350X) | 448–512 GB/s |
| On-chip SRAM/cache | TMem + L2 (per SM/die) | 256 MB Infinity Cache + per-XCD L2 | 180 MB SRAM |
| Software stack | CUDA / cuDNN / cuBLAS / TensorRT-LLM | ROCm / MILU / TensorRT-LLM | TT-Metalium / TT-NN / TT-XLA / PyTorch |

## Compute architecture

### NVIDIA Blackwell

The B200 is NVIDIA's **first chiplet GPU**: two reticle-limited dies on TSMC 4NP, packaged with CoWoS-L and unified by the NV-HBI chip-to-chip interconnect at 10 TB/s, presenting to software as a single GPU of 208 billion transistors [1][2]. Each die carries 80 Streaming Multiprocessors (SMs), for 160 SMs total; each SM holds **four fifth-generation Tensor Cores**, for 640 Tensor Cores overall [5]. The fifth-gen Tensor Cores perform a `(4×16)×(16×4)` matrix multiply in ultra-reduced FP4 precision and add a new `tcgen05.mma` warp-level instruction model, dedicated Tensor Memory (TMEM), and a hardware Decompression Engine; the die also carries a second-generation Transformer Engine that auto-converts inputs to FP4 [2]. This is the compute engine that saturates during prefill.

### AMD Instinct MI300X / MI350X

The MI300X is built on **CDNA3** and introduces the Accelerator Complex Die (**XCD**) as the primary compute building block. Each XCD has 40 Compute Units (CUs) — 38 active, 2 disabled for yield — sharing a 4 MB L2 cache; across 8 XCDs that yields **304 active CUs**, 19,456 stream processors, and 1,216 matrix cores [7][8]. The MI350X moves to **CDNA4**, which roughly doubles execution resources for sub-16-bit types and adds hardware support for OCP micro-scaling formats (MXFP8, MXFP6, MXFP4); the MI350X uses 8 XCDs of 32 active CUs each (**256 active CUs total**) with the I/O dies consolidated from four (MI300X) to two [9]. Despite being a newer node, the MI350X has fewer active CUs than the MI300X because each CDNA4 CU is more capable per unit [9]. The MI300X ships at a peak engine clock of 2,100 MHz and delivers up to 163.4 TFLOPS FP64 Matrix and over 2.6 PFLOPS FP8 [7][8].

### Tenstorrent Blackhole

Blackhole departs from the GPU paradigm: it is built around **Tensix** vector cores rather than a CUDA-style SM. The Blackhole Tensix Processor carries **120 Tensix cores** and **16 SiFive x280 "Big" RISC-V cores** that can boot Linux directly on the chip [12]. It delivers **664 TFLOPS in Block FP8** and is manufactured on TSMC 6nm [12]. Its compute model is fundamentally different from the GPU/CUDA and CDNA families, which shapes how its software stack (TT-Metalium) must schedule work.

## Chiplet / die design and interconnect

- **Blackwell:** Two reticle-limited dies unified by NV-HBI (10 TB/s chip-to-chip); the B200 is NVIDIA's first chiplet GPU. At the system level, NVLink-C2C and the NVLink Switch provide scale-up and scale-out; the GB200 Grace Blackwell Superchip pairs two B200 GPUs with one Grace CPU, and the **GB200 NVL72** scales this to 72 GPUs and 36 Grace CPUs on the largest NVLink domain NVIDIA has offered, providing **130 TB/s of GPU-to-GPU communication** [1][2][4].
- **MI300X / MI350X:** 8 XCDs plus 4 I/O dies (MI300X) or 2 I/O dies (MI350X), tied together by **Infinity Fabric** at roughly 4 TB/s in a fully-meshed topology [7][9]. The MI350X consolidates I/O-die functions into two larger dies rather than four smaller ones [9].
- **Blackhole:** Chiplet-based within a single package; the Galaxy server scales to 32 Blackhole chips with up to 56 × 800G Ethernet ports for scale-out [11].

## Memory systems

Memory is the dimension that most directly determines decode performance (see the prefill/decode section below).

| Spec | NVIDIA Blackwell (B200) | AMD MI300X | AMD MI350X | Tenstorrent Blackhole |
|---|---|---|---|---|
| Memory type | HBM3e | HBM3 | HBM3e | GDDR6 |
| Capacity | 192 GB | 192 GB | 288 GB | Up to 32 GB |
| Peak bandwidth | Up to 8 TB/s | 5.3 TB/s | 8 TB/s | 448–512 GB/s |
| Stacks | 8 | 8 (24 GB each) | 8 (36 GB each) | 256-bit bus |
| Infinity Cache / LLC | — | 256 MB | 256 MB | 180 MB SRAM |

The **B200 carries up to 192 GB of HBM3e across 8 stacks at up to 8 TB/s**, doubling the fifth-gen NVLink bandwidth to 1.8 TB/s per GPU [2]. The **MI300X** has 192 GB of HBM3 across 8 stacks (24 GB each) driven at 5.2 Gbps, for 5.325 TB/s peak theoretical bandwidth, plus a 256 MB Infinity Cache (16-way set-associative) on the I/O dies providing up to 17.2 TB/s of peak cache bandwidth [7][8]. The **MI350X** reaches 288 GB of HBM3e (36 GB per stack) at 8 Gbps for 8 TB/s peak, on the consolidated two-IOD design [9]. Tenstorrent **Blackhole uses GDDR6** (not HBM): up to 32 GB on a 256-bit bus at 16 Gbps, for 448–512 GB/s of bandwidth, backed by 180 MB of on-chip SRAM (1.5 MB per Tensix core) [12].

## Software stack

- **NVIDIA:** CUDA / cuDNN / cuBLAS with **TensorRT-LLM** as the inference runtime — the most mature ecosystem, with broad framework support and low deployment friction [23][24][25].
- **AMD:** ROCm / MILU with TensorRT-LLM support; maturing but with more deployment friction than CUDA [10][11].
- **Tenstorrent:** **TT-Metalium** (low-level kernel framework) + **TT-NN** (neural-network library) + **TT-LANG** + **TT-XLA** (which includes a **vLLM plugin**) on top of PyTorch; training is **TT-Blacksmith** [14]. The stack is purpose-built for the Tensix architecture and is less mature than CUDA, with thinner third-party benchmark coverage [14].

## Prefill vs. decode trade-offs

LLM text-generation inference decomposes into two structurally different phases with **opposite hardware bottlenecks**, cleanly captured by the *roofline model*, which classifies any computation by its **arithmetic intensity** (FLOPs per byte moved from memory):

- **Prefill is compute-bound.** The whole prompt is processed at once as one large batched matrix-matrix multiply (GEMM), giving high arithmetic intensity — roughly equal to the sequence length / batch size — that sits *above* the GPU's ridge point and saturates the tensor cores [15][16][17][19]. Prefill time ≈ FLOPs ÷ effective TFLOPS, where FLOPs scale with sequence length plus a term quadratic in sequence length (and, for mixture-of-experts models, only active parameters participate) [15].
- **Decode is memory-bandwidth-bound.** Each decode step generates exactly one token but must re-read the full model weights plus the growing KV cache from HBM, so its arithmetic intensity collapses to ~1 FLOP/byte — *below* every GPU's ridge point — making it memory-bandwidth-bound [15][16][17][19]. Decode time per token ≈ (model size in bytes) ÷ (memory bandwidth × tensor parallelism × bandwidth utilization) [15].

### Why the split matters

The two phases map directly onto the two industry-standard latency metrics: **time to first token (TTFT)** measures prefill latency, and **time per output token (TPOT)** measures decode latency [15][18]. Empirically, nearly all of prefill latency is attributable to compute, whereas in decode less than ~20% of latency is compute — the rest is memory access [15]. This is why a single GPU can sit at >90% compute utilization during a prefill burst but <5% during decode [15].

Concrete roofline numbers: on the NVIDIA H100 SXM5, the compute roof is ~989.5 TFLOPS (BF16) / ~1,979 TFLOPS (FP8) with a bandwidth roof of 3.35 TB/s, giving a **ridge point of ~295 FLOPs/byte (BF16) or ~591 FLOPs/byte (FP8)** [15][18]. A 2,048-token prompt prefill sits at ~2,048 FLOPs/byte — roughly 7× past the ridge — while decode at batch size 1 sits at ~1 FLOP/byte (~0.3% of the compute roof) and only crosses the ridge near batch ~280 [15].

### The KV cache is the decode bottleneck

The KV cache footprint is `2 × layers × kv_heads × head_dim × seq_len × bytes`, scaling linearly with batch size, sequence length, precision, layers, KV-head count, and head size [15]. This is why decode throughput is governed by memory bandwidth: `throughput = (model weights + KV cache) / (per-step latency)` [15]. As the KV cache exceeds on-chip capacity, decode forces slow HBM accesses, and decode throughput saturates and degrades as batch/context grow [15].

### Optimization levers

- **Continuous / in-flight batching** (Orca, Anyscale/vLLM): 23–36.9× throughput gain over static batching at matched latency [15].
- **PagedAttention** (vLLM): cuts KV-cache memory waste from the reported 60–80% to near-zero [15][17].
- **FlashAttention**: IO-aware tiling keeps the N×N attention scores in SRAM, avoiding O(N²) HBM traffic and lifting utilization from ~40% [15].
- **Quantization** (SmoothQuant INT8, TensorRT-LLM FP8): ~1.56× to 2.3× speedups [15].
- **Speculative decoding:** 2–3× acceleration at identical output quality [15].
- **Chunked prefill:** trades a TTFT/TPOT tradeoff — larger chunks lower TTFT but increase per-token decode time, worsening TPOT [15].
- **Prefill/decode disaggregation** (DistServe, Splitwise): heterogeneous disaggregation (e.g., A100 prefill + a fast decode engine) wins whenever the output-to-input length ratio L_out/L_in exceeds a small threshold — roughly >1/52 over 25 Gbps Ethernet or >1/203 over 100 Gbps — meaning it is beneficial for the vast majority of practical workloads and only unfavorable for extremely input-heavy tasks (e.g., binary classification over very long contexts) [15].

### The bandwidth ceiling

Peak GPU memory bandwidth has risen from 2,039 GB/s on the A100 (2020) to 4.8 TB/s on the H200 and roughly 5.3–6 TB/s on AMD's MI300X/MI325X — a ~3× rise per generation — which directly raises the decode throughput ceiling [15][17]. However, bandwidth gains show diminishing returns: quadrupling bandwidth yields only about a threefold improvement in the roofline [15]. This is why the raw HBM bandwidth figure is arguably the single most decision-relevant spec for inference workloads — more so than peak FLOPS [15][17].

## Cross-accelerator synthesis

Which accelerator leads depends on the workload profile:

- **Decode-bound, long-context, token-at-a-time workloads** favor high HBM bandwidth and large capacity. Here the **MI350X (288 GB HBM3e, 8 TB/s)** and the **B200 (192 GB HBM3e, 8 TB/s)** lead on the memory dimension that bounds decode, while Blackhole's GDDR6 (≤512 GB/s) is at a severe disadvantage — though Blackhole compensates with a large 180 MB on-chip SRAM to hold more working set on-die [12][15].
- **Prefill-bound, high-throughput batched workloads** favor raw tensor-core FLOPS and mature parallelism. Here **Blackwell** leads on maturity and raw peak performance (~98.9 PFLOPS FP8), while the **MI300X** offers large 192 GB capacity on the previous generation and the **Blackhole** trades peak FLOPS for energy efficiency and a distinct Tensix compute model [2][7][12].
- **Software maturity and ecosystem lock-in** favor **NVIDIA** (CUDA / TensorRT-LLM); **AMD** is maturing (ROCm / TensorRT-LLM); **Tenstorrent** is purpose-built but less mature, with thinner third-party benchmark coverage [14][23].

The single most common error in reading these spec sheets is conflating prefill and decode — a "tokens per second" number is meaningless without stating the model, batch size, context length, precision, and concurrency [15].

## Sources

[1] NVIDIA Blackwell Architecture: https://resources.nvidia.com/en-us-blackwell-architecture
[2] NVIDIA Blackwell Architecture Technical Brief: https://cdn.prod.website-files.com/61dda201f29b7efc52c5fbaf/6602ea9d0ce8cb73fb6de87f_nvidia-blackwell-architecture-technical-brief.pdf
[3] DGX B200 Datasheet: https://resources.nvidia.com/en-us-dgx-systems/dgx-b200-datasheet
[4] GB200 NVL72: https://www.nvidia.com/en-us/data-center/gb200-nvl72/
[5] Cornell Virtual Workshop — Blackwell B200: https://cvw.cac.cornell.edu/gpu-architecture/horizon-gpus-blackwell-b200/blackwell_b200
[6] Chips and Cheese — NVIDIA B200: https://chipsandcheese.com/p/nvidias-b200-keeping-the-cuda-juggernaut
[7] AMD CDNA 3 White Paper: https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/white-papers/amd-cdna-3-white-paper.pdf
[8] AMD Instinct MI300X Data Sheet: https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf
[9] MI350X System Acceptance Guide: https://instinct.docs.amd.com/projects/system-acceptance/en/latest/gpus/mi350x.html
[10] ROCm MI350: https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html
[11] Hot Chips 2024 — Tenstorrent (Jasmina Davor): https://hc2024.hotchips.org/assets/program/conference/day1/88_HC2024.Tenstorrent.Jasmina.Davor.v7.pdf
[12] Tenstorrent Blackhole Documentation: https://docs.tenstorrent.com/aibs/blackhole/index.html
[13] Tenstorrent Blackhole and Wormhole Architecture Explained: https://airtrain.ai/hardware/tenstorrent-blackhole-and-wormhole-architecture-explained/
[14] Tenstorrent Software Stack: https://docs.tenstorrent.com/getting-started/tt-software-stack.html
[15] Prefill/Decode-Aware Evaluation of LLM Inference on Emerging AI Accelerators (arXiv 2606.17104): https://arxiv.org/html/2606.17104v1
[16] LLM Performance Model — Prefill & Decode Estimator: https://joursbleu.github.io/llm-perf-model/
[17] GPU Inference Performance: Prefill, Decode & Batching Explained (IntuitionLabs): https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching
[18] The Roofline Model for LLM Inference (Spheron): https://www.spheron.network/blog/the-roofline-model-for-llm-inference-are-you-compute-bound-o/
[19] Prefill Is Compute-Bound, Decode Is Memory-Bound (Towards Data Science): https://towardsdatascience.com/prefill-is-compute-bound-decode-is-memory-bound-why-your-gpu-shouldnt-do-both/
[20] NVIDIA Blackwell Architecture Microbenchmark (arXiv 2512.02189): https://arxiv.org/html/2512.02189v1
[21] NVIDIA Blackwell Architecture Technical Brief (mirror): https://assets-global.website-files.com/61dda201f29b7efc52c5fbaf/6602ea9d0ce8cb73fb6de87f_nvidia-blackwell-architecture-technical-brief.pdf
[22] AMD Instinct MI350X — Full Guide (Inside Deep Tech): https://www.insidedeeptech.com/amd-instinct-mi350-cdna4-ai-gpu-full-guide/
[23] NVIDIA TensorRT-LLM Overview: https://developer.nvidia.com/tensorrt-llm
[24] NVIDIA TensorRT-LLM Documentation: https://docs.nvidia.com/tensorrt-llm/index.html
[25] NVIDIA TensorRT-LLM GitHub: https://github.com/NVIDIA/TensorRT-LLM
[26] vLLM TT Plugin: https://vllm.ai/blog/2026-09-07-vllm-tt-plugin
