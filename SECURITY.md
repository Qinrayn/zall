# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in zall, please report it by emailing the maintainers directly. **Do not** file a public GitHub issue.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 48 hours. We take all security reports seriously and will work to address verified vulnerabilities promptly.

## Scope

Security issues in zall include:
- Prompt injection or jailbreak risks in the agent loop
- Unauthorized file access via tool calls
- Chain-hash verification bypass
- Authentication or API key leakage
- MCP server credential exposure

## Safe Harbor

We consider the following out of scope:
- Theoretical vulnerabilities without a practical exploit
- Issues in third-party dependencies (report upstream)
- Social engineering of zall users

## Preferred Configuration

For production use:
1. Always set `ZALL_API_KEY` via environment variable (not config.toml)
2. Review `~/.zall/rules.toml` to ensure appropriate tool access controls
3. Run `zall /doctor` to verify configuration integrity
4. Use `/plan` mode for read-only code review sessions