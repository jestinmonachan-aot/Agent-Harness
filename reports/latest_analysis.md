# Vulnerability Analysis Report

**Repo:** https://github.com/glpi-project/glpi

## Findings

This confirms the CSRF architecture is sound â€” no finding here. I have sufficient coverage now to compile the report.

## Security Assessment â€” GLPI (`11.0/bugfixes`)

Codebase identified as **GLPI** (open-source IT asset/service management, PHP), on a fully modernized stack (PHP â‰¥8.2, Symfony 6.4 components, PHPUnit 11). Overall posture is strong â€” this is a mature project with an active security disclosure process (`SECURITY.md`). No urgent blockers found, but several items are worth tracking for the migration.

**1. Dependencies â€” clean, no action needed**
`composer audit --locked` and `npm audit` against the committed lockfiles both return **zero known vulnerabilities**. All major deps (Symfony 6.4, Guzzle 7.15, TCPDF 6.11, Twig 3.27, PHPSpreadsheet 5.1, sabre/dav 4.7) are current major versions, not EOL branches. `laminas/laminas-mail` and `laminas/laminas-loader` are flagged **abandoned** upstream but explicitly allow-listed in `composer.json` (`ignore-abandoned`) â€” worth a forward-looking replacement plan since abandoned packages won't receive future CVE patches.

**2. Hardcoded credentials â€” dev-only, but flag for migration hygiene**
- `docker-compose.yaml`: weak default creds (`glpi`/`glpi` DB user+root, LDAP `admin`/`admin`) â€” standard for the local dev compose stack, not shipped to production config, low risk but confirm no CI/staging environment reuses this compose file unmodified.
- `install/empty_data.php:9419-9421`: seeds a default `glpi`/`glpi` account (properly hashed via `password_hash`) as part of the fresh-install fixture data â€” this is intentional GLPI installer behavior (along with `tech`/`post-only`/`normal` defaults), but **must be rotated/disabled before any migrated instance goes live**, since it's a well-known target for credential-stuffing scans against GLPI installs.

**3. Authentication â€” solid**
`src/Auth.php` uses `password_hash()`/`password_verify()` with `PASSWORD_DEFAULT` (bcrypt/argon2 per PHP config) â€” no legacy MD5/SHA1 password hashing found anywhere in `src/`. TOTP 2FA support present (`Glpi\Security\TOTPManager`).

**4. CSRF â€” centralized correctly**
CSRF is enforced via a single Symfony kernel listener (`CheckCsrfListener`) that fires on every main request, including legacy `front/*.php` and `ajax/*.php` scripts, which are routed through the same HTTP kernel/firewall (`Glpi\Http\Firewall`, `RequestRouterTrait`) rather than being separate unprotected entry points. No bypass found.

**5. Injection surfaces checked â€” no exploitable pattern found**
- No raw string-concatenated SQL reaching `mysqli::query()`; the handful of direct `->query()` calls in `DBmysql.php` are internal driver plumbing fed by the parameterized query builder, with dynamic values passed through `$this->quote()`.
- `eval()`/`unserialize()` hits in `src/` are false positives (`DOMXPath::query()`, a JS-string comment mentioning `eval()`), not PHP code execution sinks.
- Sampled `ajax/*.php` scripts using `$_POST` string concatenation apply proper sanitization (`Html::cleanId()`, `(int)` casts) before use.
- LDAP auth (`AuthLDAP.php`) isn't deeply audited here â€” worth a dedicated pass on `ldap_search`/`ldap_bind` filter construction if LDAP auth is in scope for the migrated environment, since LDAP filter injection is a recurring historical bug class for this integration.

**6. Migration-blocker considerations (not vulnerabilities, but relevant)**
- Runtime requires PHP â‰¥8.2 and Node â‰¥20.9 â€” confirm target infra meets this before cutover.
- `laminas/laminas-mail` abandoned status (see #1) is the one dependency-freshness item that should have an owner/ticket rather than a silent audit-suppression.

**Bottom line:** no critical/high-severity code vulnerabilities or outdated-CVE dependencies found in this snapshot. The only real pre-migration action items are (a) confirming default `glpi`/`glpi`/`admin` seed credentials get rotated in any migrated environment, and (b) tracking a replacement for the abandoned `laminas/laminas-mail` dependency.
