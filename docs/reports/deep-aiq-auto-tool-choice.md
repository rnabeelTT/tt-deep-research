# Tenstorrent Blackhole, NVIDIA Blackwell, and AMD MI350 for LLM Inference

A comparative analysis of three data-center accelerators — Tenstorrent **Blackhole**, NVIDIA **Blackwell** (B200/GB200), and AMD **MI350** (MI350X/MI355X) — focused on their architectures, memory systems, software stacks, and the prefill-vs-decode performance trade-offs that define LLM inference. This report distinguishes Blackhole (P100) from its predecessor Wormhole (B0), identifies the B200/GB200 as the relevant NVIDIA data-center inference part, and treats marketing performance multipliers with appropriate skepticism.

## Architectures

**Tenstorrent Blackhole** is the third-generation AI accelerator architecture, succeeding Grayskull and Wormhole, fabricated on TSMC's 6 nm process versus Wormhole's 12 nm [1][2][3]. It is fundamentally a RISC-V multiprocessor with specialized matrix engines: counting every core on the die, Blackhole contains 752 "baby" RISC-V cores plus 16 large SiFive cores for 768 RISC-V cores in all [3]. The architectural philosophy carries heavily from Wormhole — the Tensix tile concept, the baby RISC-V orchestrator cores, GDDR6 memory, and the Ethernet-based scale-out fabric — while the major differences lie in compute density, on-chip memory, network bandwidth, and a new on-die CPU island [1][3].

Blackhole A0 contains 140 physical Tensix++ worker tiles versus Wormhole B0's 80 Tensix+ tiles. All commercial Blackhole cards ship with only 120 of the 140 cores enabled, a binning/firmware decision [3]. Each Tensix tile contains ~1.5 MB (1464 KiB) of L1 SRAM — explicitly a scratchpad, not a cache (there is no cache anywhere on the card), five baby RISC-V cores (RV32IM: B, T0, T1, T2, NC) that act as orchestrators issuing matrix/vector instructions and managing data movement, and a Tensix coprocessor [1][3]. The coprocessor has three frontend pipelines (one per thread, executing up to one instruction per thread per cycle) and a shared backend with seven execution units: a Matrix Unit (MVMUL, DOTPV), a Vector Unit (SFPMAD, SFPLOAD, SFPLUT), two Unpackers, four Packers, a Scalar Unit, a Mover for L1-to-L1 DMA, and a Miscellaneous Unit [3]. The MOP expander expands a single macro-op into up to 32,639 instructions [1]. Baby RISC-V cores run at 1.35 GHz in Blackhole versus 1 GHz in Wormhole [3].

The most visible architectural change is the new on-die CPU island: 16 SiFive Intelligence X280 cores organized into four clusters of four, which can run a full Linux distribution directly on the accelerator (demonstrated in Tenstorrent's public `tt-bh-linux` repository) [3]. This removes the host PC from the critical path for latency-sensitive, small-batch workloads.

| Feature | Tenstorrent Blackhole | NVIDIA Blackwell (B200) | AMD MI350X/MI355X |
|---|---|---|---|
| Process node | TSMC 6 nm | TSMC 4NP (custom 4N) | TSMC N3P (XCD) / N6 (IOD) [3][7] |
| Design | 140 Tensix++ tiles (120 shipped) | Dual-die, 208B transistors | 8 XCDs + 2 IODs chiplet [3][7] |
| Compute | 745 TFLOPS FP8 (silicon), 664 shipped | ~9,000 TFLOPS FP8 | Up to 4x MI300X peak [3][9] |
| Cache | 1.5 MB L1 SRAM/tile (no cache) | Tensor Cores 5th-gen | 256 MB Infinity Cache [3][7] |
| Scale-out | 10× 400 GbE Ethernet | NVLink 5 + NVLink-C2C | Infinity Fabric 38.4 Gbps [3][7] |

**NVIDIA Blackwell** packs 208 billion transistors, fabricated on a custom TSMC 4NP process (an enhancement of the 4N node used for Hopper and Ada Lovelace) [4][6]. All Blackwell products feature two reticle-limited dies connected by a 10 TB/s chip-to-chip interconnect in a unified single GPU, placed on a TSMC CoWoS-L 2.5D interposer [4]. The B200 is the standalone Blackwell GPU with 192 GB of HBM3e memory and up to 8 TB/s of memory bandwidth, fifth-generation Tensor Cores, a second-generation Transformer Engine (FP4), and a hardware Decompression Engine [4][6]. The GB200 Grace Blackwell Superchip pairs one Grace CPU with two B200 GPUs over NVLink-C2C, and the GB200 NVL72 rack-scale system combines 36 Grace CPUs and 72 Blackwell GPUs for 13.4 TB of HBM3e and 1.44 exaFLOPS FP4 sparse (720 petaFLOPS dense) compute [4][6]. Blackwell delivers roughly 2x H100 training performance, 2.4x the memory bandwidth, and up to 4x faster LLM inference [4][6].

**AMD MI350** (launched June 12, 2025) is the fourth-generation CDNA4 chiplet architecture, integrating 8 accelerator complex dies (XCDs) on TSMC's N3P process and 2 I/O dies (IODs) on TSMC's N6 process, tied together by AMD Infinity Fabric [3][7]. Each GPU has 256 active compute units (1024 matrix cores, 16,384 stream processors), a 256 MB Infinity Cache, and 288 GB HBM3E across 8 stacks of 12-Hi HBM3E at 8 Gbps, delivering 8 TB/s of peak memory bandwidth [3][7]. A key CDNA4 change is that AMD consolidated the four smaller IODs of the prior generation into two larger IODs connected by a direct link roughly 14% faster than CDNA3, reducing latency and power [7]. The MI355X (1400W, direct liquid-cooled) roughly doubles the compute throughput of the MI325X for reduced-precision datatypes and supports the new industry-standard FP4/FP6 microscaling formats at over 10 TFLOP/s peak [3][7].

## Memory Systems

| Feature | Tenstorrent Blackhole | NVIDIA Blackwell (B200) | AMD MI350X/MI355X |
|---|---|---|---|
| Memory type | GDDR6 (deliberate) | HBM3e | HBM3e [3][7] |
| Capacity | 32 GB/chip | 192 GB | 288 GB [3][7] |
| Bandwidth | 512 GB/s | 8 TB/s | 8 TB/s [3][7] |
| Controllers | 8 × 4 GB | — | 8 stacks × 36 GB [3][7] |

Tenstorrent's choice of GDDR6 rather than HBM is deliberate, intended to hit aggressive price points and avoid HBM3/HBM3e supply constraints [1][3]. Blackhole ships 32 GB of GDDR6 organized across eight 4 GB memory controllers at 512 GB/s peak bandwidth (the p100a SKU ships 28 GB across seven controllers; the p150 ships 32 GB across eight) [3]. Wormhole had 12 GB of GDDR6 across six 2 GB controllers at ~336 GB/s peak [3].

NVIDIA's B200 has 192 GB HBM3e (vs H100's 80 GB HBM3) and 8.0 TB/s bandwidth (vs H100's 3.35 TB/s), a +139–140% improvement [6]. AMD's MI350 series delivers 288 GB HBM3E across 8 stacks of 12-Hi HBM3E at 8 Gbps, with 8 TB/s of peak memory bandwidth [7].

## Software Stacks

**Tenstorrent** uses a layered stack: users enter at the top with TT-Forge and descend to lower layers (TT-NN, TT-Metalium) only when they need finer control [2]. Blackhole adds TT-Forge and TT-NN on top of the shared TT-Metalium runtime (Wormhole used TT-Buda + TT-Metalium) [3].

**NVIDIA** offers TensorRT-LLM (open-source, PyTorch-native) and vLLM, delivering large performance gains on Blackwell via NVFP4 4-bit quantization, pipeline/tensor/expert parallelism, in-flight batching, PagedAttention, disaggregated serving, and multi-token prediction [6][11].

**AMD** uses ROCm 7, integrating vLLM, SGLang, Triton, AITER, RCCL, and llm-d. AMD reports ~90–95% of CUDA throughput on standard PyTorch vLLM inference [7].

## Prefill vs Decode Trade-offs

LLM inference decomposes into two phases with opposite bottlenecks: **prefill is compute-bound** (matrix-matrix GEMM, high arithmetic intensity ~100–1000 FLOPs/byte, saturates tensor cores) while **decode is memory-bandwidth-bound** (matrix-vector GEMV, low arithmetic intensity ~1–2 FLOPs/byte, saturates HBM). The KV cache is the linchpin: prefill builds it and decode must re-read the entire cache every step, so decode's bottleneck is the rate at which bytes move off HBM.

This asymmetry drives every major serving optimization: FlashAttention/chunked prefill/prefix caching for prefill; batching, quantization, and speculative decoding for decode. The single most impactful optimization for decode throughput is batching — batching 32 sequences increases arithmetic intensity 32×. Speculative decoding: one benchmark on Llama 3.3 70B reported a 3.55× speedup, but the draft model's own latency becomes the new bottleneck. Quantization benchmarks: 4-bit KV cache (INT4) reported 57% decode latency reduction on LLaMA3-8B.

## Interconnects

| Feature | Tenstorrent Blackhole | NVIDIA Blackwell | AMD MI350 |
|---|---|---|---|
| Scale-out | Ethernet fabric (no NVLink/IB) | NVLink 5 (1.8 TB/s) + NVLink Switch (130 TB/s) | Infinity Fabric 38.4 Gbps [3][7] |
| GPU-to-GPU | 10× 400 GbE | NVLink 5 | Infinity Fabric [3][7] |

Tenstorrent's architectural bet is an on-die/off-die Ethernet fabric [3]. Each Blackhole ASIC provides 10 × 400 GbE links for 32 TB/s of on-die accelerator fabric, and the Galaxy server scales out via up to 56 × 800 GbE QSFP-DD ports for 11.2 TB/s cluster scale-out bandwidth [3]. Because each Blackhole chip carries its own Ethernet endpoints, the mesh forms a single coherent compute domain without needing external switch silicon.

NVIDIA's approach is the opposite: the GB200 pairs one Grace CPU with two B200 GPUs over NVLink-C2C (GraceLink), delivering 900 GB/s bidirectional bandwidth, plus NVLink 5 for 1.8 TB/s GPU-to-GPU and NVLink Switch enabling 130 TB/s in a 72-GPU NVL72 domain [4][6].

## Pricing

| Product | Price | Notes |
|---|---|---|
| Tenstorrent Galaxy Blackhole entry server | $110,000–$160,000 | 32 ASICs, 23 PFLOPS Block FP8 [3] |
| Tenstorrent Galaxy 4-rack supercluster | $440,000–$640,000 | [3] |
| Tenstorrent p150 developer card | $1,399 | [3] |
| NVIDIA B200 (pure GPU) | ~$2/hour | 192GB HBM3e [3] |
| NVIDIA GB200 Superchip | ~$12–20/hour | [3] |
| AMD Instinct (per GPU-hour) | $0.95–$8.60 | provider-dependent [7] |

Tenstorrent claims ~$6 per million tokens for inference versus ~$30 per million tokens for an NVIDIA GB300 system — a claimed ~5x lower cost-per-token and ~5x TCO advantage [3]. These figures come from TT Deploy livestream demos and are not independently verified [3]. AMD reports MI355X at 0.173 per million tokens via MoRI SGLang versus B200 TRT-LLM 0.178 and B200 SGLang 0.284, and up to 40% better tokens per dollar on Llama 3.1 405B FP4 [7].

## Recommendations

- **Prefill-bound / high-throughput batch inference:** NVIDIA Blackwell (B200/GB200) with TensorRT-LLM, leveraging NVFP4 quantization and disaggregated serving.
- **Memory-bandwidth-bound decode / cost-sensitive scale-out:** Tenstorrent Blackhole's Ethernet fabric and GDDR6 positioning, or AMD MI355X for competitive TCO.
- **Mixture-of-Experts inference:** NVIDIA Blackwell NVL72 for the largest MoE models (1.8T parameters), with up to 2.8x per-GPU throughput gains on DeepSeek-R1 [6].

The MI355X delivers up to 4x peak theoretical performance over MI300X and up to 4.2x real-world Llama 3.1 405B inference gains [7].

## Sources

[1] tenstorrent.com: https://tenstorrent.com/en/solutions/llm-inference
[2] docs.tenstorrent.com: https://docs.tenstorrent.com/getting-started/vLLM-servers.html
[3] tenstorrent.com: https://tenstorrent.com/en/hardware/galaxy
[4] resources.nvidia.com: https://resources.nvidia.com/en-us-inference-team-lead/blackwell-architecture
[5] nvidia.github.io: https://nvidia.github.io/TensorRT-LLM/overview.html
[6] developer.nvidia.com: https://developer.nvidia.com/blog/nvidia-gb200-nvl72-delivers-trillion-parameter-llm-training-and-real-time-inference
[7] rocm.docs.amd.com: https://rocm.docs.amd.com/en/develop/reference/gpu-arch/mi350.html
[8] amd.com: https://www.amd.com/en/products/accelerators/instinct/mi350.html
[9] towardsdatascience.com: https://towardsdatascience.com/prefill-is-compute-bound-decode-is-memory-bound-why-your-gpu-shouldnt-do-both/
[10] redis.io: https://redis.io/blog/prefill-vs-decode/
[11] github.com: https://github.com/z-lab/dflash
[12] machinelearningmastery.com: https://machinelearningmastery.com/from-prompt-to-prediction-understanding-prefill-decode-and-the-kv-cache-in-llms/
[13] tenstorrent.com: https://tenstorrent.com/en/hardware/cards
[14] spheron.network: https://www.spheron.network/blog/tenstorrent-vs-nvidia-open-source-ai-hardware/
[15] amd.com: https://www.amd.com/en/blogs/2025/amd-instinct-mi350-series-game-changer.html
