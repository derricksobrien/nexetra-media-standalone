# Security & Compliance Checklist

**Last Updated:** 2026-06-08

This document outlines security practices for the Nexetra Media Factory pipeline.

## Critical Rules

1. **Never commit secrets**
   - API keys, tokens, passwords must live in `.env` or environment variables
   - All `.env*` files are gitignored
   - Use managed secrets vaults (GitHub Secrets, vault.nexetra.online, etc.) for CI/CD

2. **SSH/Private keys**
   - Store in `~/.ssh/` or vault — never in repo
   - Lab machine SSH keys: use key-based auth without passphrase only on trusted internal networks
   - Rotate quarterly

3. **Model downloads**
   - Ollama/ComfyUI models can exceed 50GB per model; never commit
   - Store in `.gitignored` model/ folder with local cache reference
   - Use model registry/hash to track which versions are deployed

4. **Output artifacts**
   - Video files, audio, frames, and temp working files all go to `output/` (gitignored)
   - Only commit job specs and run summaries to `jobs/` and `logs/`
   - Run summaries should not leak customer data or internal metrics

5. **Configuration files**
   - Template configs can be committed (e.g., `config.example.json`)
   - Real configs with credentials go to `.env` or `config.local.json`
   - Use environment variable substitution in code, not hardcoded values

## Pre-Commit Checklist

Before pushing code to main:
- [ ] No `.env` or `.env.local` files staged
- [ ] No `*.pem`, `*.key`, `secrets.json` in diff
- [ ] No API keys, tokens, or passwords in code comments
- [ ] No model weights or large binary files staged
- [ ] `git diff --cached` shows only code, docs, configs (no credentials)

## Lab-Specific

- Mac Mini 01-06: use SSH key auth with `~/.ssh/id_nexetra` (internal network only)
- DGX Spark & ubuntu-1: restrict SSH to lab subnet (10.0.0.0/24)
- Ollama instances: run without external network exposure (local port binding only)
- ComfyUI: no public port binding; access only from orchestrator or admin workstation

## Credential Rotation

- SSH keys: rotate every 90 days or after staff change
- Claude API key (if used): rotate every 6 months
- GitHub token: rotate every year or if exposed

## Evidence & Audit Trail

- All runs logged in `logs/YYYYMMDD-HHMMSS/` with stage logs and manifests
- Manifests record input scripts and output checksums (not PII)
- Retention: keep 30 days locally, archive to cold storage after
- No customer data embedded in video frames or audio files

## Incident Response

If you suspect a leak:
1. Immediately rotate affected credentials
2. Scan recent commits with `git log -p` for exposed secrets
3. Use BFG repo cleaner or git-filter to strip history if needed
4. Force-push to main (coordinated with team)
5. Notify affected stakeholders

## Tools

- `git-secrets` hook can auto-detect common patterns before commit
- `truffleHog` or `detect-secrets` for scanning history
- `.env` validation: ensure all required vars present before pipeline starts

## References

- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)
- [Credential Rotation Best Practice](https://pages.nist.gov/800-63-3/sp800-63b.html#sec5)
