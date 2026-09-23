# Vulnerability Analysis Report

**Repo:** https://github.com/glpi-project/glpi

## Findings

Good, dependabot and SECURITY.md are present. I have enough to compile the findings.

## Security Analysis: GLPI (11.0.10-dev, branch `11.0/bugfixes`)

**Overall posture:** This is a mature, actively-maintained codebase with good security hygiene already in place â€” not a typical "legacy" system with latent rot. Findings below are mostly low-severity or environmental, not exploitable code-level flaws.

### Dependencies
- `composer audit --locked` â†’ **0 advisories**. `npm audit` â†’ **0 vulnerabilities**. Both lockfiles are current at HEAD.
- `dependabot.yml` present and configured â€” dependency drift will be caught automatically going forward.
- Two vendor patches are auto-applied post-install (`tools/patches/guzzlehttp-guzzle-restrict-http-methods.patch`, `laminas-mail-invalid-header-ignore.patch`) â€” confirm these still apply cleanly after any dependency bump; a silently-failing patch would reintroduce the issues they fix.
- `composer.json` marks `laminas/laminas-mail` as an ignored-abandoned package (`audit.ignore-abandoned`) â€” it's unmaintained upstream. Worth tracking as a migration risk since it won't receive future security patches.

### Injection risk
- Direct SQL: `DBmysql::query()` is intentionally hard-disabled (`throw new Exception('Executing direct queries is not allowed!')`), forcing all DB access through the parameterized query builder (`DBmysql::doQuery()` + builder methods) â€” good defense-in-depth against SQL injection.
- LDAP: filter construction in `src/AuthLDAP.php` consistently uses `ldap_escape($value, '', LDAP_ESCAPE_FILTER)` before interpolating user input into filter strings (lines ~3467, ~3840) â€” no obvious LDAP injection path in the spots checked.
- No use of `eval()`, `system()`, `exec()`, `passthru()`, or `shell_exec()` found in `src/` during targeted search.

### XML parsing (XXE)
- `src/Glpi/Agent/Communication/AbstractRequest.php` (inventory agent XML ingestion) and `src/KnowbaseItem.php` (HTML sanitization) use `simplexml_load_string()` / `DOMDocument` without explicit `LIBXML_NONET`/entity-loader hardening. Not currently exploitable since PHP â‰¥8.2 is required (composer.json) and libxml has disabled external entity loading by default since PHP 5.4.30/8.0 â€” but this is implicit safety from the PHP version floor, not explicit code defense. Worth a comment/assertion if the migration ever considers supporting older PHP.

### Auth / session
- `session.cookie_httponly` is force-set via `SystemConfigurator`; `session.cookie_secure` is checked at runtime with an explicit warning (`SessionsSecurityConfiguration`) if HTTPS is in use but the flag isn't set â€” reasonable posture, though it's advisory rather than enforced.
- 2FA (`TOTPManager`), CAS, SAML/OAuth2 (`league/oauth2-*`), and Altcha CAPTCHA are all present as first-class, actively-versioned dependencies.

### Hardcoded secrets
- No secrets found in `config/` (properly gitignored â€” only `.gitkeep` tracked; real config is generated at install time).
- `docker-compose.yaml` and `tests/e2e/.env` contain **weak default credentials** (`glpi/glpi`, LDAP `admin/admin`) â€” these are dev/CI-only and not shipped to production, but flag explicitly if this compose file is ever reused as a deployment template.

### Migration-blocking observations
- **Local PHP mismatch**: the PHP binary on this machine is 7.2.34, but `composer.json` requires `php: >=8.2`. Not a codebase vulnerability, but this environment cannot run the app as-is â€” flag before any local verification/testing step.
- `.phpstan-baseline.php` is ~20,000 lines / 873 KB of suppressed static-analysis findings (plus a 1.5 MB `missingType.iterableValue` baseline). These are pre-existing type-safety gaps that PHPStan would otherwise flag â€” not confirmed vulnerabilities, but a large blind spot that should be burned down incrementally during modernization rather than treated as permanently suppressed.
- `laminas/laminas-mail` (abandoned upstream, see above) is a concrete dependency-lifecycle risk for the migration plan.

### Not found / ruled out
- No hardcoded API keys/passwords in source.
- No raw `unserialize()` of untrusted input in the areas searched.
- No evidence of disabled TLS verification or auth bypass flags in the files reviewed.

**Recommendation:** given the clean audit results and disciplined query-builder/escaping patterns, prioritize (1) a plan to replace or vendor-fork `laminas/laminas-mail`, (2) reducing the PHPStan baseline rather than growing it further, and (3) confirming the two vendor patches survive the next dependency bump â€” rather than searching for injection-class bugs, which this pass didn't surface.
