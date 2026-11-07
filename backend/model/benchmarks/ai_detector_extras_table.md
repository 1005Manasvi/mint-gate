| Candidate | CPU ms | test AUC | Flux (test_unseen) flagged | OOS-select bal. acc @review | OOS-select AUC | OOS AI flagged | OOS genuine flagged (NFT / pixel / painting / photo) | Δ bal. acc vs B/32 [95 % CI] | beats B/32 |
|---|---|---|---|---|---|---|---|---|---|
| clip-b32+siglip2-b16 | ? | 0.992 | 98.4 % | 81.5 % | 0.919 [0.904, 0.933] | 81.8 % | 10.8 / 30.0 / 0.0 / 34.7 | +2.0 pp [-0.9, +5.0] | no |
| siglip2-b16 | ? | 0.991 | 97.8 % | 81.0 % | 0.908 [0.892, 0.923] | 80.2 % | 9.6 / 30.0 / 2.0 / 31.2 | +1.5 pp [-1.6, +4.7] | no |
| clip-b32 | ? | 0.979 | 87.2 % | 79.5 % | 0.891 [0.873, 0.908] | 72.4 % | 9.3 / 20.0 / 1.3 / 22.6 | baseline | - |
