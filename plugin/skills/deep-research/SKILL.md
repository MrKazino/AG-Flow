---
name: deep-research
description: Answer a question from sources you actually read, with a claim ledger that gives every statement its source and a confidence level, and says "unknown" where the sources run out. Use for "how does X work", library or API comparisons, version-specific behaviour, anything about Antigravity, an SDK or a CLI that may have changed, and any question where a confident guess would be costly.
---

# Deep research

The failure to avoid is a fluent answer that nobody checked. The remedy is procedural: every statement in the answer is traceable to something you opened, and what you could not find is said out loud.

## Procedure
1. **State the question and the decision it feeds.** What would change if the answer were different? Write the exact version, platform and scope that matter (library version, Antigravity version, OS).
2. **Inventory sources, closest to the truth first:**
   - the code or config in front of you (`grep_search`, then `view_file` on the relevant range);
   - what is installed: `--version`, `--help`, the package's own docs and source for the installed version;
   - official documentation for that exact version (`read_url_content`, `search_web`);
   - everything else (blogs, forum answers, your memory): leads only, never the basis of a claim.
3. **Read, don't skim.** Open the page or range that contains the claim. A search snippet is a pointer, not a source.
4. **Keep a ledger** while you work: each claim, its source (`path:line`, or URL with the date you read it), and how you know it (below).
5. **Cross-check the claims that matter** with a second independent source, or by running something small. If two sources disagree, record both, find which is newer or closer to the code, and say which you trust and why.
6. **Answer from the ledger.** Lead with the answer, then the evidence, then what is unknown and how to find out.

## How you know it (use these labels)
- **ran** - you executed it and saw the result in this session;
- **read** - you opened the source or documentation and the text says it;
- **inferred** - it follows from things you read or ran; name them;
- **unknown** - you looked and found nothing, or did not look. Say which.

## Rules
- No claim without a source. If you cannot name one, the claim is `unknown` or it is dropped.
- Never invent a path, function, flag, option, URL, version number or quote. If it matters, look it up; if it does not exist, say that.
- Prefer the version the user has over the latest version, and say which one you read.
- Dates matter: a page you read today can still describe an old release. Note the version it documents.
- Stop when the question is answered to the confidence the decision needs, not when you run out of ideas. Say what residual doubt remains.

## Output
```
Answer: <one or two sentences>

| Claim | Source | How known |
|---|---|---|
| ... | path:line or URL (read on <date>) | ran / read / inferred |

Unknown / not verified: <list, with how to settle each>
Conflicts between sources: <list, or none>
```
