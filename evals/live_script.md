# Live reading script

Read each line as one dictation (hold the key, speak, release). Speak naturally; don't slow down for the model. Start talking
right as you press the key, and let go right after the last word — that is exactly what the pre-roll and tail are for.
With `[audio] keep_recordings` on, every take lands in `~/.murmur/recordings/` as `<ts>.wav` + `<ts>.txt` (raw transcript), so
the audio can be re-run through other models (`python -m evals.stt_compare`) and scored against these lines.

Punctuation in brackets is spoken: say the words in brackets.

1. What you'll see on launch now: a grey [quote] Starting [end quote] card whose bar fills steadily over four seconds.
2. We can always adjust later on if need be.
3. Push the fix to staging first, then tag version two point three point one.
4. The meeting moved from 2:30 to 3:45, so grab the room on the fourth floor.
5. Send it to ethan at example dot com and cc the launch channel.
6. The repo is github dot com slash volcanic m g slash murmur.
7. The budget is 0.43, actually 0.44, per unit.
8. I think we should, um, scratch that. Let's start with the onboarding flow.
9. Groceries: pineapples, pears, peaches, plums [new line] and a bag of oranges.
10. Hey, can you take a look at the PvP mechanics before lunch?
11. Set the timeout to 1500 milliseconds and the retry count to 3.
12. Dear Dr. Nguyen, thank you for the quick turnaround on the Q3 report.
13. She asked, [quote] are we shipping Thursday? [end quote], and nobody answered.
14. Rename max retry count to MAX_RETRIES in the config loader. (code tone: say "max retry count", "max underscore retries")
15. First paragraph ends here. [new paragraph] Second paragraph starts with a lowercase test.

Hard cases (expect some misses — they tell us which model wins):

16. Kubernetes, Postgres, Obsidian, LM Studio, Parakeet, Wispr Flow.
17. Fifteen fifty, fifty fifteen, one five one five.
18. It's their turn there; they're already here.
