Prompt version: 2.

VAL3 settler. You read one slice of a coding-agent session, the candidate entries an earlier call
proposed from it, and, under each candidate, the stored entries close to it. Decide, for each
candidate listed, whether it is new or repeats or replaces its close entries. Answer only through
the JSON schema: {"decisions": [{"candidate": <its number>, "decision": "new | repeat | replaces |
undecided", "refs": [<refs of close entries>]}]}.

Rules
- The slice's most recent measurement is the current state.
- First decide, for each close entry, whether it is about the same thing as the candidate: the same
  object (the same tool, account, setting, criterion, preference), the same property of it and the
  same scope. Only then compare what each says about it.
- new: no close entry is about the same thing. Another object, another property, another scope or a
  problem and its fix are different things, even on the same topic. refs is empty.
- repeat: one close entry is about the same thing and already says everything the candidate says:
  each assertion of the candidate follows from that entry. A narrower restatement is a repeat. A
  candidate that adds a value, a changed status, a return value, a condition, a scope, a tool's
  environment or a recipe step that the entry does not state is not a repeat. refs holds exactly
  that one ref.
- replaces: the candidate states the current value or status of what each named close entry states,
  and keeps every part of that entry that still holds. refs holds one or more refs, all of them close
  entries of that candidate.
- undecided: a close entry is about the same thing and the candidate neither repeats nor replaces it:
  for instance it changes a value or a status ("proposed" to "required") but drops a part of the entry
  that still holds, or adds a detail while leaving out others. Also undecided when the slice cannot
  tell. refs is empty.
- One decision covers all close entries of a candidate: when any close entry about the same thing
  would be undecided, the decision is undecided, even if the candidate repeats or replaces another.
- Never rewrite a candidate: judge it as written.
- Exactly one decision per candidate listed; none for a candidate that is not listed.
