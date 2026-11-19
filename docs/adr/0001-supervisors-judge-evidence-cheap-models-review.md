# Supervisors judge evidence; cheap models do all review

Frontier-model quota is the Factory's scarcest resource, so Orchestrators judge Reviewer findings, Verification Evidence and the diffstat instead of reading diffs, and read hunks only for Protected Surface or files a finding points at. Every review pass, local (`code-review` run by the Worker) and in CI, runs on a cheap model. Mandatory Verification Evidence is the counterweight to the bugs that no Reviewer flags and no Supervisor sees.
