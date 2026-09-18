# LLM interpretation ranking

Generated: 2026-09-18T15:10:09.626326+00:00

Dataset: 10 cases, 18 notes per model. Timeout: 12.0s per HTTP call.

Each candidate runs independently with native output attempted first. No cache, consensus, retries, repair, or cross-model failover is used. Explicit unsupported-format responses allow one text call; latency includes that call. Transport errors count as failures. JSON validity means the entire response is a strict JSON object; recoverable JSON is reported separately in the JSON artifact. Hours and numeric accuracy use only applicable notes, and require the correct directive type. Numeric tolerance: absolute/relative 1e-6.

Ranking: full semantic accuracy, valid directive rate, strict JSON rate, then successful-response median latency. Defaults require full coverage and at least 90% full semantic and strict JSON accuracy. Public-sample performance does not establish hidden-test accuracy.

| Rank | Provider | Model | JSON | Type | Hours | Numbers | Full | Median ms | Errors | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | experimentallab | gpt-5.6-luna | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 2015 | 0 | completed |
| 2 | experimentallab | deepseek-v4.1-flash | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 3332 | 0 | completed |
| 3 | experimentallab | qwen3.8-27b | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 5869 | 0 | completed |
| 4 | openrouter | deepseek/deepseek-v4-flash-0731:free | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 9840 | 0 | completed |
| 5 | experimentallab | deepseek-v4-flash | 94.4% | 94.4% | 92.9% | 100.0% | 94.4% | 4072 | 1 | completed |
| 6 | openrouter | openrouter/free | 44.4% | 44.4% | 42.9% | 44.4% | 44.4% | 8247 | 10 | completed |
| 7 | openrouter | nex-agi/nex-n2.5-mini:free | 38.9% | 38.9% | 35.7% | 44.4% | 38.9% | 3359 | 11 | completed |
| 8 | nararouter | agnes-2.5-flash | 27.8% | 22.2% | 21.4% | 11.1% | 22.2% | 5811 | 13 | completed |
| 9 | openrouter | nvidia/nemotron-3-super-120b-a12b:free | 16.7% | 16.7% | 14.3% | 11.1% | 16.7% | 3330 | 15 | completed |
| 10 | openrouter | z-ai/glm-5.2:free | 11.1% | 11.1% | 14.3% | 11.1% | 11.1% | 2575 | 16 | completed |
| 11 | nararouter | nemotron-3-ultra-free | 5.6% | 5.6% | 0.0% | 0.0% | 5.6% | 8715 | 17 | completed |
| 12 | openrouter | nvidia/nemotron-3-ultra-550b-a55b:free | 5.6% | 5.6% | 0.0% | 0.0% | 5.6% | 27484 | 17 | completed |
| 13 | anthropic | claude-sonnet-5 | N/A | N/A | N/A | N/A | N/A | N/A | 0 | missing_credentials |
| 14 | nararouter | ling-3.0-flash-fin-free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 15 | nararouter | ling-3.0-flash-sante-free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 16 | nararouter | ling-3.0-flash-vl-free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 17 | nararouter | nemotron-3-super-free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 18 | nararouter | nemotron-3.5-lightning-free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 19 | openai | gpt-4o-mini | N/A | N/A | N/A | N/A | N/A | N/A | 0 | missing_credentials |
| 20 | openrouter | cohere/north-mini-code:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 21 | openrouter | dots-studio/dots-3-note-preview:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 22 | openrouter | google/gemma-4-26b-a4b-it:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 23 | openrouter | google/gemma-4-31b-it:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 24 | openrouter | inclusionai/ling-3.0-flash-fin:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 25 | openrouter | inclusionai/ling-3.0-flash-sante:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 26 | openrouter | inclusionai/ling-3.0-flash-vl:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 27 | openrouter | liquid/lfm-2.5-2.6b:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 28 | openrouter | nex-agi/nex-n2.5-pro:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 29 | openrouter | nvidia/nemotron-3.5-lightning:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 30 | openrouter | poolside/laguna-s-2.1:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 31 | openrouter | poolside/laguna-xs-2.1:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 32 | openrouter | qwen/qwen3.8-27b:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 33 | openrouter | thinkingmachines/inkling-small:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |
| 34 | openrouter | thinkingmachines/inkling:free | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | N/A | 18 | completed |

## Selected defaults

- primary: experimentallab/gpt-5.6-luna
- fast: experimentallab/gpt-5.6-luna
- arbiter: openrouter/deepseek/deepseek-v4-flash-0731:free
