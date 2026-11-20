# Security

Report a vulnerability privately through GitHub: the repository's **Security** tab, **Report a vulnerability**.
Please do not open a public issue for it.

Say which version (`val3 --version`), what an attacker can do and how to reproduce it. VAL3 is experimental and
kept by one maintainer: reports are read and answered, without a fixed response time. Only the latest published
version gets fixes.

What VAL3 touches, to help judge a report: it runs as the user who installed it, from Claude Code hooks and from
`claude -p` for capture; it stores what sessions learn in the PostgreSQL vaults it is pointed at; `val3 view`
serves on 127.0.0.1 only.
