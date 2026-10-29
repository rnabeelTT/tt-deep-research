# Tenstorrent Blackhole vs. NVIDIA Blackwell vs. AMD MI350 for LLM Inference

A comparative architecture, memory, and software-stack analysis of three 2024–2025 accelerator families for large-language-model inference.

## TL;DR

The three chips sit in fundamentally different design philosophies. **NVIDIA Blackwell (B200/GB200)** and **AMD Instinct MI350 (MI350X)** are HBM3e-powered data-center accelerators optimized for the bandwidth-hungry decode phase, while **Tenstorrent Blackhole** deliberately uses GDDR6 to win on price-per-token rather than raw throughput. For inference specifically, the decisive architectural fact is that *decode* is memory-bandwidth-bound and *prefill* is compute-bound — so Blackwell's and MI350X's multi-terabyte HBM3e budgets matter far more for real-world token generation than their headline FLOP counts, whereas Tenstorrent's lower bandwidth is offset by its cost advantage and its software stack's handling of the KV cache.

---

## 1. Die Architecture

**NVIDIA Blackwell (B200).** The B200 is a dual-die design: two reticle-limit dies bonded together via the NVIDIA High-Bandwidth Interface (NV-HBI) fabric at ~10 TB/s, presenting as a single coherent CUDA device. The combined structure contains 208 billion transistors organized into 8 GPCs and 148 Streaming Multiprocessors (SMs), with 4 L2 cache partitions (double Hopper's count) and 8 HBM3e memory stacks. Each SM has a dedicated 256 KB Tensor Memory (TMEM). The compute capability is `sm_100`, and the 5th-generation Tensor Cores replace the warp-synchronous `mma.sync`/`wgmma` instructions with a single-thread `tcgen05.mma` instruction, enabling per-thread scheduling. Inside the GB200 Superchip, the B200 is physically identical to the discrete HGX-B200 SXM6 module; the superchip differs only in power delivery, cooling, and the NVLink mezzanine attachment. [W586efbd0.8], [W8420eac3.7], [Wbdac37ac.1], [W0cbdae2d.4], [W0cbdae2d.9]

**AMD Instinct MI350 (MI350X).** The MI350X uses a chiplet-based, heterogeneous CDNA4 design: 8 accelerator complex dies (XCDs) plus 2 I/O dies (IODs) on-package. The XCDs hold the compute logic and lowest cache levels on TSMC N3P (3 nm); the IODs contain the Infinity Cache, memory controllers, and interconnects on the mature TSMC N6 (6 nm) process. Total transistor count is 185 billion. The two IODs are directly connected (versus four smaller IODs on the MI300X), reducing latency and power. Each XCD contains 36 CDNA4 Compute Units (CUs) — four arrays of nine — of which 32 are active, for 256 active CUs total across the part, equaling 16,384 stream processors and 1,024 Matrix Cores. The MI350P is a smaller 4-XCD + 1-IOD configuration. [Wbd73a1f1.0], [Wbd73a1f1.1], [W76e49e53.1]

**Tenstorrent Blackhole.** Blackhole is Tenstorrent's third-generation architecture (successor to Grayskull and Wormhole), built on TSMC 6 nm. The chip is a grid of Tensix++ compute cores interconnected by a Network-on-Chip (NoC). Each Tensix++ core is a small heterogeneous cluster containing five baby RISC-V cores, a tile-based math engine, a vector math engine, a pair of NoC routers, and a block of L1 SRAM. Compared to Wormhole's Tensix+, Blackhole increases L1 SRAM per tile and makes the NoC wider and faster, reducing contention for shared bandwidth. A distinguishing feature is that it scales out via standard Ethernet rather than a proprietary interconnect. [Waa99682b.5], [W39ce4c71.0], [Waa99682b.6], [Waa99682b.9]

| Feature | Tenstorrent Blackhole | NVIDIA B200 | AMD MI350X |
|---|---|---|---|
| Node | TSMC 6 nm | TSMC 4NP (dual die) | TSMC N3P (XCD) + N6 (IOD) |
| Package | Single die | Dual-die (NV-HBI) | 8×XCD + 2×IOD |
| Compute units | 120 Tensix++ cores (140 on die) | 148 SMs, 16,896 CUDA cores | 256 active CUs, 16,384 SPs |
| Total transistors | Not published | 208 billion | 185 billion |

## 2. Compute (FLOPs)

**Tenstorrent Blackhole.** Silicon-level peak is 745 TFLOPS FP8 (BLOCKFP8) and 372 TFLOPS FP16; the shipped commercial card is rated at 664 TFLOPS BLOCKFP8. A higher-bin "Galaxy" 32-chip system reaches ~23 PFLOPS FP8 dense total (~719 TFLOPS per ASIC). Tenstorrent publishes no sparse FLOP rate. [Waa99682b.5], [Waa99682b.6], [Waa99682b.12], [Wb2d2bb07.0], [Wb2d2bb07.2]

**NVIDIA B200.** Dense FP8 is 4,500 TFLOPS; dense BF16/FP16 is 2,250 TFLOPS; new dense FP4 is 9,000 TFLOPS. Measured effective throughput at large matrix dims hits ~96–99% of peak (FP8 ≈ 3,850 TFLOPS), indicating memory bandwidth — not compute — is the bottleneck. Per GB200 superchip (2× B200): FP8 ~9,000 dense / ~18,000 sparse. Per NVL72 rack (72 GPUs): NVFP4 1,440 PFLOPS (sparse) / 720 dense; FP8 720 PFLOPS (sparse) / 360 dense. [Wbdac37ac.1], [W586efbd0.42], [W8420eac3.35], [W0cbdae2d.4], [W7609bbd8.4]

**AMD MI350X.** OCP-FP8 (E5M2/E4M3) is 4.6 PFLOPS dense / 9.2 PFLOPS with structured sparsity; MXFP8 is also 4.6 PFLOPS; BF16 is 2.3 PFLOPS dense / 4.6 sparse. FP32 is 144.2 TFLOPS, FP64 72.1 TFLOPS. [W76e49e53.0], [Wbd73a1f1.3]

| Precision | Tenstorrent Blackhole | NVIDIA B200 | AMD MI350X |
|---|---|---|---|
| FP8 (dense) | 664 TFLOPS | 4,500 TFLOPS | 4,600 TFLOPS |
| FP8 (sparse) | Not published | ~9,000 TFLOPS | 9,200 TFLOPS |
| BF16 (dense) | 372 TFLOPS | 2,250 TFLOPS | 2,300 TFLOPS |
| FP4 (dense) | Not published | 9,000 TFLOPS | Not published |

The takeaway: Blackwell and MI350X are within ~2× of each other on dense FP8, and both vastly outperform Blackhole on raw FLOPs. But as the section below explains, FLOP counts are the *wrong* metric for the decode phase.

## 3. Memory Systems

This is where the three designs diverge most sharply and where the inference-relevant differences live.

**Tenstorrent Blackhole.** Uses **GDDR6** (not HBM) with a 256-bit bus at 16 GT/sec. The p150 cards ship 32 GB GDDR6 at 512 GB/s; the p100a ships 28 GB at 448 GB/s. On-die SRAM is 180 MB (1.5 MB per Tensix core). The GDDR6 choice is deliberate — it hits aggressive price points and sidesteps HBM3/HBM3e supply constraints. [W4562decd.2], [Waa99682b.1], [Waa99682b.8], [Waa99682b.9]

**NVIDIA B200.** Uses **HBM3e**: 8 stacks totaling 192 GB at **8.0 TB/s** peak. Grace CPU adds on-package LPDDR5X (~480 GB at ~512 GB/s). Per GB200 superchip: 384 GB HBM3e at 16 TB/s. Per NVL72 rack: ~13.8 TB of pooled HBM3e addressable over a single NVLink domain. [Wbdac37ac.1], [W586efbd0.8], [W8420eac3.7], [Wb810a6da.0], [W0cbdae2d.0], [W0cbdae2d.9]

**AMD MI350X.** Uses **HBM3e**: 8 stacks of 12-Hi, 36 GB per stack, 288 GB total at **8 TB/s** peak over an 8,192-bit interface at 8 GHz. [Wbd73a1f1.0], [Wbd73a1f1.2], [W76e49e53.1]

| Memory | Tenstorrent | NVIDIA B200 | AMD MI350X |
|---|---|---|---|
| Type | GDDR6 | HBM3e | HBM3e |
| Bus | 256-bit | (8-stack) | 8,192-bit |
| Capacity | 28–32 GB | 192 GB | 288 GB |
| Bandwidth | 448–512 GB/s | 8.0 TB/s | 8 TB/s |

Blackwell and MI350X are within ~2× on bandwidth and both massively exceed Blackhole's GDDR6. That gap is exactly why the memory system matters more than FLOPs for inference — see Section 5.

## 4. Interconnect

**Tenstorrent Blackhole.** Scales out via standard Ethernet rather than a proprietary fabric: 10 links at 400 Gbps each per ASIC (~1 TB/s off-chip). On-die NoC routing is dimension-ordered with wrap-around (toroidal topology); NoC link bandwidth is 60.9 bytes/cycle (2× Wormhole's). The Galaxy system is a 32-chip 4×8 mesh forming a single coherent compute domain without external switch silicon, carrying up to 11.2 TB/s across 56 × 800 GbE QSFP-DD ports. [Waa99682b.1], [Waa99682b.9], [Wa7e50ccf.2], [Wa7e50ccf.5], [Wb2d2bb07.2]

**NVIDIA B200.** NVLink 5th-gen delivers 1.8 TB/s per GPU bidirectional (2× H100's NVLink 4). NVLink-C2C connects the Grace CPU to each B200 at 900 GB/s with full cache coherence and unified virtual memory. The NVL72 rack offers the largest NVLink domain ever: 130 TB/s across 72 GPUs, built from 9 fifth-gen NVSwitch trays. Scale-out beyond the rack uses ConnectX-8 SuperNICs at 800 Gb/s. [Wbdac37ac.14], [W8420eac3.4], [Wb810a6da.2], [W7609bbd8.0], [Wd9ba7dcc.3], [Wd9ba7dcc.6]

**AMD MI350X.** Each GPU connects to host via one PCIe Gen 5 x16 link; 7 Infinity Fabric links run at 38.4 Gbps (up from 32 Gbps on MI300X). Peak Infinity Fabric link bandwidth is 153 GB/s per GPU; scale-up ~153 GB/s, scale-out ~128 GB/s. A fully connected 8-GPU node ring delivers >1 TB/s aggregate communication bandwidth per GPU. [Wbd73a1f1.3], [W76e49e53.1], [Wd7d400d6.5]

| Interconnect | Tenstorrent | NVIDIA | AMD |
|---|---|---|---|
| On-die / rack | Toroidal NoC + Ethernet | NVLink 5 + NVSwitch | Infinity Fabric |
| Scale-out | 10 × 400 GbE | 800 Gb/s SuperNIC | 38.4 Gbps IF links |
| Rack-level | 11.2 TB/s (Galaxy) | 130 TB/s (NVL72) | >1 TB/s/GPU (8-GPU ring) |

## 5. Prefill vs. Decode: The Core Trade-Off

This is the conceptual heart of the comparison and the reason the three architectures diverge in practice.

**The two phases.** LLM inference decomposes into two physically distinct phases. **Prefill** processes the entire input prompt in one parallel pass — computing Q/K/V for all tokens, running full self-attention, generating and caching the KV cache, and outputting the first token. It maps to the user-facing **TTFT (Time To First Token)**. **Decode** is the autoregressive phase that generates one token at a time, reading the full KV cache and weights each step to produce the next token; it maps to **TPS (Tokens Per Second)** and **ITL (Inter-Token Latency)**. Prefill can process every input token simultaneously because none depends on a not-yet-generated token; decode is sequential because each new token causally depends on all preceding tokens. [Wa03d5283.0], [Wa03d5283.2], [Wa03d5283.9], [W7a9e56ea.2], [W7a9e56ea.9], [Wff3a3fd9.5]

**Why prefill is compute-bound.** Prefill's core work is large-scale matrix-matrix multiplication (GEMM) — e.g., QKV projections `Q = X·W^Q`. Its arithmetic intensity is high: `AI_prefill = 2n / sizeof(dtype)`. For a 1024-token prompt in FP16, AI ≈ 1024 FLOPs/Byte, far exceeding a modern GPU's compute-to-bandwidth ridge point (e.g., ~295–312 FLOPs/Byte for A100/H100). Because B·T_in (batch × prompt length) is large, arithmetic intensity reaches thousands of FLOPs/Byte, saturating the tensor cores. Prefill typically runs at 50–75% of peak Model FLOPs Utilization (MFU); its time scales with total token count and complexity, not memory bandwidth. Measured utilization during prefill is ~90–95%. [Wa03d5283.1], [Wff3a3fd9.4], [Wff3a3fd9.5], [Wb70a2c6d.3], [Wccb069a3.4]

**Why decode is memory-bound.** Decode degenerates the GEMM into a matrix-vector multiplication (GEMV): a single activation vector `x_t ∈ R^(1×d)` passes through every linear layer. Its arithmetic intensity is near-unity — for FP16 (2 bytes/param), `AI_decode ≈ 1 FLOP/Byte`, roughly 100× below a data-center GPU's ridge point, so tensor cores idle while HBM streams. Each decode step must read the entire accumulated KV cache from HBM into SRAM to compute attention, then write the new K/V back. The KV cache size is `2·B·S·L·H_kv·d_h·P` bytes — linear in batch, sequence length, layers, KV heads, and head size — so as S grows into tens/hundreds of thousands of tokens, KV-cache bytes can surpass the model-weight bytes read per step, further depressing arithmetic intensity. Measured decode utilization is only 20–40%; the memory bus saturates while compute sits idle. The single-stream decode ceiling is `bandwidth / (num_params × bytes_per_param)`. [Wff3a3fd9.5], [W703366c6.48], [W703366c6.14], [W703366c6.18]

**The roofline framework.** The roofline model gives the unifying equation: `P_attainable = min(P_peak, I × B_mem)`; the ridge point is `I_ridge = P_peak / B_mem`. If arithmetic intensity I > I_ridge → compute-bound (prefill); if I < I_ridge → memory-bound (decode). This is the theoretical basis for why prefill and decode favor different hardware characteristics. [Wff3a3fd9.1], [Wff3a3fd9.2], [Wff3a3fd9.11]

**The key insight — batching helps prefill but not decode.** Batching raises decode's arithmetic intensity to `I ≈ B FLOP/Byte`, but only until B approaches the ridge point (~300). Crucially, batching *amortizes the weight read but not the KV-cache read* — each sequence owns a private cache, so decode attention's arithmetic intensity `≈ 2·H_q/(H_kv·P)` is independent of both batch size B and sequence length S (the S cancels, B never appears). For a Llama-3-8B-style model (H_q=32, H_kv=8, bf16) this is 4 FLOP/byte; for full multi-head attention it collapses to 2/P = 1 — so decode attention is *permanently* memory-bound at any batch size. This is why GQA/MLA speed up decode (cutting H_kv by group size g raises intensity by g and cuts bytes by g), and why FlashDecoding splits the KV cache to fill the GPU at batch 1. [W703366c6.14], [W703366c6.18]

**Why this matters across the three chips.** Reducing memory bandwidth by 40% only increases prefill latency by ~17%; reducing compute by 50% only increases decode latency by ~22%. So Blackwell's and MI350X's multi-terabyte HBM3e budgets dominate decode performance, while Blackhole's lower GDDR6 bandwidth is a smaller penalty for its workload mix. This is the basis for "right-sizing": separate prefill chips (high TFLOP/s, less HBM) and decode chips (max HBM bandwidth), and for disaggregated serving (Splitwise, DistServe) which reported 7.4× more requests / 12.6× tighter SLO adherence. [Wb70a2c6d.3], [Wccb069a3.0], [Wff3a3fd9.11]

**Empirical inference numbers.** In an 8×7B model at FP8, Blackwell delivers 51,200 tok/s (1.58× vs H200); at FP4, 112,800 tok/s (2.5× vs FP16). NVIDIA reports H100 (FP8) achieving up to 4.6× max throughput and 4.4× faster 1st-token latency than A100. [W586efbd0.45], [W7a9e56ea.38]

## 6. Software Stacks

**Tenstorrent (tt-llm / tt-metal + TT-Torch).** tt-metal provides `PrefillRuntime` and `DecodeRuntime`, paged KV cache abstractions, a vLLM adapter, and a `Generator` class orchestrating both phases; it supports chunked prefill for long sequences and multi-user batch scheduling. Distributed multi-host prefill pipelines use a ring-buffer producer/consumer with manifest-driven spatial/tensor parallelism. Paged KV caching divides the cache into blocks mapped via page tables to physical device memory pages, enabling very long contexts. TT-Torch is a PyTorch frontend compiler that runs standard PyTorch models on Tenstorrent accelerators and supports TorchInductor compilation. A Springer paper systematically characterizes LLM inference on Tenstorrent's Wormhole accelerator, analyzing decode by batch size, KV cache size, and processor count to identify bottlenecks. [W380203ea.0], [W380203ea.1], [W380203ea.2], [W380203ea.3], [Wf4d628b6], [W0b8776d8]

**NVIDIA (CUDA + TensorRT-LLM + Triton).** TensorRT-LLM is built on PyTorch and incorporates state-of-the-art optimizations for efficient inference, served via Triton Inference Server. Triton-based FlashAttention kernels reduce attention HBM accesses from O(n²) to O(n). NVIDIA built the Dynamo framework around disaggregated serving. [W463a2e01], [Wa03d5283.6], [Wb70a2c6d.2], [Wccb069a3.6]

**AMD (ROCm + Triton/Wave).** ROCm's LLM optimization covers PyTorch 2 compile mode (TorchInductor → Triton), Flash Attention v2, paged attention, PyTorch TunableOp (rocBLAS/hipBLASLt GEMMs), and multi-GPU inference. On MI210, prefill with large batch uses large GEMM matrices → compute-bound; decode is a tall-and-skinny GEMV → memory-bandwidth-bound. Flash Attention v2 and Xformers are effective for prefill but "not effective during output decoding, as the sequence length is just 1." PyTorch TunableOp shows significant prefill gains and, with paged attention, reduces decode latency for tall/skinny GEMV. AMD cites MLPerf Inference's interactive latency-constrained scenarios as the comparison framework. [Wc9209982.4], [Wc9209982.5], [Wc9209982.7], [Wc9209982.9], [W72d6f2b9.0], [W72d6f2b9.1], [W72d6f2b9.6], [W7a9e56ea.35]

| Dimension | Tenstorrent | NVIDIA | AMD |
|---|---|---|---|
| Low-level | tt-metal | CUDA | ROCm |
| LLM framework | tt-llm | TensorRT-LLM | (custom/ROCm) |
| Compiler | TT-Torch / TorchInductor | Triton | Triton (Wave) |
| KV cache | Paged (vLLM adapter) | Paged attention | Paged attention |
| Benchmark | — | MLPerf | MLPerf |

## 7. Synthesis

1. **Memory is the story for decode.** Blackwell (8.0 TB/s) and MI350X (8 TB/s) are within ~2× and both exceed Blackhole's GDDR6 (512 GB/s) by ~16×. Because decode is permanently memory-bandwidth-bound regardless of batch size, those two are the natural choices for high-throughput token generation, while Blackhole's bandwidth is a genuine constraint for long-context decode — but it is the price Tenstorrent pays to hit aggressive price points. [Wbdac37ac.1], [Wbd73a1f1.2], [Waa99682b.8], [W703366c6.14]

2. **FLOPs matter for prefill.** Blackwell and MI350X are within ~2× on dense FP8 (4,500 vs 4,600 TFLOPS) and both vastly outperform Blackhole (664 TFLOPS). Since prefill is compute-bound and saturates the tensor cores, this gap is real but modest between the two HBM parts. [Wbdac37ac.1], [W76e49e53.0], [Wff3a3fd9.5]

3. **The architectures reflect their memory choices.** Blackhole's Ethernet-based scale-out and GDDR6 signal a cost/efficiency-first philosophy; Blackwell's NVLink-C2C coherence and MI350X's chiplet Infinity Fabric signal bandwidth-first design. [Waa99682b.9], [W7609bbd8.0], [Wbd73a1f1.3]

4. **Software stacks converge on the same fixes.** All three rely on paged KV caching, and NVIDIA/AMD both use Triton-based FlashAttention to cut attention's HBM traffic from O(n²) to O(n) — acknowledging the same decode-bound reality. [Wa03d5283.6], [W380203ea.3], [Wc9209982.9]

---

## Sources
- [W0b8776d8] Performance Characterization and Optimization of LLM ... - https://link.springer.com/chapter/10.1007/978-3-032-35251-4_32
- [W0cbdae2d.0] NVIDIA GB200 Grace Blackwell Superchip - https://yobitel.com/knowledge-base/nvidia-gb200
- [W0cbdae2d.4] NVIDIA GB200 Grace Blackwell Superchip - https://yobitel.com/knowledge-base/nvidia-gb200
- [W0cbdae2d.9] NVIDIA GB200 Grace Blackwell Superchip - https://yobitel.com/knowledge-base/nvidia-gb200
- [W380203ea.0] Common LLM Runtime and Prefill Framework | tenstorrent/tt-metal | DeepWiki - https://deepwiki.com/tenstorrent/tt-metal/7.8-common-llm-runtime-and-prefill-framework
- [W380203ea.1] Common LLM Runtime and Prefill Framework | tenstorrent/tt-metal | DeepWiki - https://deepwiki.com/tenstorrent/tt-metal/7.8-common-llm-runtime-and-prefill-framework
- [W380203ea.2] Common LLM Runtime and Prefill Framework | tenstorrent/tt-metal | DeepWiki - https://deepwiki.com/tenstorrent/tt-metal/7.8-common-llm-runtime-and-prefill-framework
- [W380203ea.3] Common LLM Runtime and Prefill Framework | tenstorrent/tt-metal | DeepWiki - https://deepwiki.com/tenstorrent/tt-metal/7.8-common-llm-runtime-and-prefill-framework
- [W39ce4c71.0] Hardware Architectures: Grayskull, Wormhole, and Blackhole | ultimateabhi/tt-metal | DeepWiki - https://deepwiki.com/ultimateabhi/tt-metal/1.2-hardware-architectures:-grayskull-wormhole-and-blackhole
- [W4562decd.2] Blackhole® PCIe Cards - https://docs.tenstorrent.com/aibs/blackhole/index.html
- [W463a2e01] TensorRT-LLM User Guide — NVIDIA Triton Inference Server - https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/getting_started/trtllm_user_guide.html
- [W586efbd0.42] Microbenchmarking NVIDIA’s Blackwell Architecture: An in-depth Architectural Analysis - https://arxiv.org/html/2512.02189v1
- [W586efbd0.45] Microbenchmarking NVIDIA’s Blackwell Architecture: An in-depth Architectural Analysis - https://arxiv.org/html/2512.02189v1
- [W586efbd0.8] Microbenchmarking NVIDIA’s Blackwell Architecture: An in-depth Architectural Analysis - https://arxiv.org/html/2512.02189v1
- [W703366c6.14] The Anatomy of LLM Inference: Prefill, Decode & The KV Cache — The LLM Stack - https://prakashkagitha.github.io/llm-stack-book/07-inference-serving/01-anatomy-inference.html
- [W703366c6.18] The Anatomy of LLM Inference: Prefill, Decode & The KV Cache — The LLM Stack - https://prakashkagitha.github.io/llm-stack-book/07-inference-serving/01-anatomy-inference.html
- [W703366c6.48] The Anatomy of LLM Inference: Prefill, Decode & The KV Cache — The LLM Stack - https://prakashkagitha.github.io/llm-stack-book/07-inference-serving/01-anatomy-inference.html
- [W72d6f2b9.0] Optimizing Triton kernels - https://rocm.docs.amd.com/en/docs-6.1.0/how-to/llm-fine-tuning-optimization/optimizing-triton-kernel.html
- [W72d6f2b9.6] Optimizing Triton kernels - https://rocm.docs.amd.com/en/docs-6.1.0/how-to/llm-fine-tuning-optimization/optimizing-triton-kernel.html
- [W7609bbd8.0] NVIDIA GB200 NVL72 - https://www.nvidia.com/en-us/data-center/gb200-nvl72/
- [W7609bbd8.4] NVIDIA GB200 NVL72 - https://www.nvidia.com/en-us/data-center/gb200-nvl72/
- [W76e49e53.0] AMD Instinct™ MI350X GPUs - https://www.amd.com/en/products/accelerators/instinct/mi350/mi350x.html
- [W76e49e53.1] AMD Instinct™ MI350X GPUs - https://www.amd.com/en/products/accelerators/instinct/mi350/mi350x.html
- [W7a9e56ea.2] GPU Inference Performance: Prefill, Decode & Batching Explained | IntuitionLabs - https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching
- [W7a9e56ea.35] GPU Inference Performance: Prefill, Decode & Batching Explained | IntuitionLabs - https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching
- [W7a9e56ea.38] GPU Inference Performance: Prefill, Decode & Batching Explained | IntuitionLabs - https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching
- [W7a9e56ea.9] GPU Inference Performance: Prefill, Decode & Batching Explained | IntuitionLabs - https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching
- [W8420eac3.35] 2512.02189 - https://arxiv.org/pdf/2512.02189
- [W8420eac3.7] 2512.02189 - https://arxiv.org/pdf/2512.02189
- [Wa03d5283.0] Prefill vs Decode Phases | LLM Learning - https://jonathanding.github.io/llm-learning/en/articles/prefill-vs-decode/
- [Wa03d5283.1] Prefill vs Decode Phases | LLM Learning - https://jonathanding.github.io/llm-learning/en/articles/prefill-vs-decode/
- [Wa03d5283.6] Prefill vs Decode Phases | LLM Learning - https://jonathanding.github.io/llm-learning/en/articles/prefill-vs-decode/
- [Wa03d5283.9] Prefill vs Decode Phases | LLM Learning - https://jonathanding.github.io/llm-learning/en/articles/prefill-vs-decode/
- [Wa7e50ccf.2] Blackhole Implementation | tenstorrent/tt-npe | DeepWiki - https://deepwiki.com/tenstorrent/tt-npe/5.3-blackhole-implementation
- [Wa7e50ccf.5] Blackhole Implementation | tenstorrent/tt-npe | DeepWiki - https://deepwiki.com/tenstorrent/tt-npe/5.3-blackhole-implementation
- [Waa99682b.1] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Waa99682b.12] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Waa99682b.5] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Waa99682b.6] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Waa99682b.8] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Waa99682b.9] Blackhole (Tenstorrent) | AI Wiki - https://aiwiki.ai/wiki/blackhole_tenstorrent
- [Wb2d2bb07.0] Tenstorrent Galaxy Blackhole: 32x Blackhole System Specs - https://flopper.io/system/tenstorrent-galaxy-blackhole
- [Wb2d2bb07.2] Tenstorrent Galaxy Blackhole: 32x Blackhole System Specs - https://flopper.io/system/tenstorrent-galaxy-blackhole
- [Wb70a2c6d.2] Prefill Is Compute-Bound. Decode Is Memory-Bound. Why Your GPU Shouldn’t Do Both. - https://towardsdatascience.com/prefill-is-compute-bound-decode-is-memory-bound-why-your-gpu-shouldnt-do-both/
- [Wb70a2c6d.3] Prefill Is Compute-Bound. Decode Is Memory-Bound. Why Your GPU Shouldn’t Do Both. - https://towardsdatascience.com/prefill-is-compute-bound-decode-is-memory-bound-why-your-gpu-shouldnt-do-both/
- [Wb810a6da.0] Cornell Virtual Workshop > Understanding GPU Architecture > Horizon GPUs: Blackwell B200 - https://cvw.cac.cornell.edu/gpu-architecture/horizon-gpus-blackwell-b200/b200_mem_nvlink5
- [Wb810a6da.2] Cornell Virtual Workshop > Understanding GPU Architecture > Horizon GPUs: Blackwell B200 - https://cvw.cac.cornell.edu/gpu-architecture/horizon-gpus-blackwell-b200/b200_mem_nvlink5
- [Wbd73a1f1.0] AMD Instinct MI350 Series microarchitecture - https://rocm.docs.amd.com/en/develop/reference/gpu-arch/mi350.html
- [Wbd73a1f1.1] AMD Instinct MI350 Series microarchitecture - https://rocm.docs.amd.com/en/develop/reference/gpu-arch/mi350.html
- [Wbd73a1f1.2] AMD Instinct MI350 Series microarchitecture - https://rocm.docs.amd.com/en/develop/reference/gpu-arch/mi350.html
- [Wbd73a1f1.3] AMD Instinct MI350 Series microarchitecture - https://rocm.docs.amd.com/en/develop/reference/gpu-arch/mi350.html
- [Wbdac37ac.1] NVIDIA B200: 192GB Specs, Benchmarks, vs H100 | Spheron - https://www.spheron.network/blog/nvidia-b200-complete-guide/
- [Wbdac37ac.14] NVIDIA B200: 192GB Specs, Benchmarks, vs H100 | Spheron - https://www.spheron.network/blog/nvidia-b200-complete-guide/
- [Wc9209982.4] Large language model inference optimizations on AMD GPUs - https://rocm.blogs.amd.com/artificial-intelligence/llm-inference-optimize/README.html
- [Wc9209982.7] Large language model inference optimizations on AMD GPUs - https://rocm.blogs.amd.com/artificial-intelligence/llm-inference-optimize/README.html
- [Wc9209982.9] Large language model inference optimizations on AMD GPUs - https://rocm.blogs.amd.com/artificial-intelligence/llm-inference-optimize/README.html
- [Wccb069a3.0] Prefill vs Decode: The Two Phases of LLM Inference | WEKA - https://www.weka.io/learn/prefill-and-decode
- [Wccb069a3.4] Prefill vs Decode: The Two Phases of LLM Inference | WEKA - https://www.weka.io/learn/prefill-and-decode
- [Wccb069a3.6] Prefill vs Decode: The Two Phases of LLM Inference | WEKA - https://www.weka.io/learn/prefill-and-decode
- [Wd7d400d6.5] AMD Instinct MI350: A Full Guide to CDNA 4 AI GPUs When NVIDIA Is Not the Only Rack Option - https://www.insidedeeptech.com/amd-instinct-mi350-cdna4-ai-gpu-full-guide/
- [Wd9ba7dcc.3] NVIDIA GB200 NVL72 Specs & Datasheet (72-GPU Rack) | Pantheon - https://pantheon.run/learn/nvidia-gb200-nvl72-specs
- [Wd9ba7dcc.6] NVIDIA GB200 NVL72 Specs & Datasheet (72-GPU Rack) | Pantheon - https://pantheon.run/learn/nvidia-gb200-nvl72-specs
- [Wf4d628b6] GitHub - tenstorrent/tt-torch: Frontend integration for ... - https://github.com/tenstorrent/tt-torch
- [Wff3a3fd9.1] Arithmetic Intensity and the Roofline Model: Why LLM Generation Is Memory-Bound and Prefill Is Compute-Bound - https://www.llms.blog/posts/arithmetic-intensity-and-the-roofline-model-why-llm-generation-is-memory-bound-and-prefill-is-compute-bound
- [Wff3a3fd9.11] Arithmetic Intensity and the Roofline Model: Why LLM Generation Is Memory-Bound and Prefill Is Compute-Bound - https://www.llms.blog/posts/arithmetic-intensity-and-the-roofline-model-why-llm-generation-is-memory-bound-and-prefill-is-compute-bound
- [Wff3a3fd9.4] Arithmetic Intensity and the Roofline Model: Why LLM Generation Is Memory-Bound and Prefill Is Compute-Bound - https://www.llms.blog/posts/arithmetic-intensity-and-the-roofline-model-why-llm-generation-is-memory-bound-and-prefill-is-compute-bound
- [Wff3a3fd9.5] Arithmetic Intensity and the Roofline Model: Why LLM Generation Is Memory-Bound and Prefill Is Compute-Bound - https://www.llms.blog/posts/arithmetic-intensity-and-the-roofline-model-why-llm-generation-is-memory-bound-and-prefill-is-compute-bound