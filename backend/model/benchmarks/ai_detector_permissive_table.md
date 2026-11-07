| Candidate | CPU ms | test AUC | Flux (test_unseen) flagged | OOS-select bal. acc @review | OOS-select AUC | OOS AI flagged | OOS genuine flagged (NFT / pixel / painting / photo) | Δ bal. acc vs B/32 [95 % CI] | beats B/32 |
|---|---|---|---|---|---|---|---|---|---|
| siglip2-b16 | ? | 0.936 | 99.3 % | 81.6 % | 0.919 [0.903, 0.934] | 80.8 % | 9.3 / 26.7 / 0.7 / 33.7 | +0.0 pp [-2.4, +2.6] | no |
| clip-b32 | ? | 0.922 | 98.8 % | 81.5 % | 0.913 [0.896, 0.929] | 80.5 % | 9.6 / 26.7 / 0.7 / 32.7 | baseline | - |
