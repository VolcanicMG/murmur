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

What you'll see on launch now, a gray "starting" card whose bar fills steadily over four seconds.
Number 2. We can always adjust later if need be.
Number 3. Push a fix to staging first, then tag version 2.3.1.
Number 4. The meeting moved from 2:30 to 3:45. So grab the room on the fourth floor. Okay.
Number 5. Send it to Ethan at example.com and CC the launch channel.
Number 6. The repo is github.com/volcanic-mg/murmur.
Number 7. The budget is 0.44 per unit.
Number 8. Let's start with the onboarding flow.
Number 9. Groceries: pineapples, pears, peaches, plums, and a bag of oranges.
Number 10. Hey, can you take a look at the PvP mechanics before launch?
Number 11. Set the timeout to 1500 milliseconds and retry count to 3.
Number 12, dear Mr. He Jun, thank you for the quick turnaround on the Q3 report.
Number 13. She asked, "Are we shipping Thursday?" And nobody answered. Yeah.
Number 14. Rename `maxRetryCount` to `maxEntries` in the config loader. Code tone say `maxEntryCount_max_retries`.
Number 15. First paragraph ends here.

Second paragraph starts with a lowercase test.
Number 16, Kubernetes, Postgres, Obsion, Elm Studios, Parker Set, Whisperflow.
Number seventeen fifteen, fifty three, fifty, fifteen, one, five, one, five.
It's their turn there. They're already there.

This is a test seeing if we can get this to work properly.
Set max retries to 5 and time out to 150 milliseconds
I push the fix to GitHub, then ping Pete about the Postgres migration.
Ship Day Friday. So let's plan for that.
Our PvP Q hit 1600 players last night.
Compare Parakeet with Wispr Flow on the same recording.
Then write it in Obsidian.
* Fix the card flicker.
* Test the hockeys.
* Ship it.
So I was thinking, we should move the meeting to next Thursday the 12th.
Email VolcanicMG, the logs for murmur, and the Lamna server.

* Buy milk
* Call mom
* Book flights
We had eggs, bacon, cheese, sandwiches.
Eggs, bacon, cheese, sandwiches
* By milk
* Call mom.
1. The way to do things is the positive way.
2. Let's see what we can do next.

Please provide the text you would like me to make into a list.
Speed about the release.
Ask Pete about the release.
We went to the pet store.
Both podsteads are down.
The quote said, "merge it today," and nobody argued.
Set the cache to 300 megabytes.
Send it Wednesday morning.
* Update the reme
* Bump the version
* Tag the release
Podstead and I tested it in LM Studio and then in Obsidian.
Bill took 3.5 seconds on GitHub Actions.
Call Dr. Nagian at 2:30 PM about the Kubernetes outage.

1. Eggs
2. bacon
3. cheese
4. sandwiches
Dark brew starts to steam,
Waking up the sleepy soul,
Morning's warm embrace.

I test it in LM Studio.Pete. I tested it in LM Studio.Both Podsteads are down.* Buy milk.
* Call mom.
Watch the live card.1. Book the room.
2. Send the invite.
3. Order lunch.

Email Dr. Yu Gien the GitHub link.
Set the timeout to 300 milliseconds.
Number 16 is my favorite.

Testing testing one two three. I'm checking to see if the dictation works or not.
What do you think the word per minute is here? Is that gonna be faster than 300 or is it gonna be slower than 200?
Do you think this is gonna work out properly or it's just gonna bump up GPU usage at all? How fast does this thing really go?
Param bam bum bum bam bum
I can't write a song, but I can certainly help you write lyrics, structure a song, or even generate code related to music generation if that's what you're looking for. What kind of song were you thinking of?

To center a div, you can use a few different CSS methods, depending on whether you want to center it horizontally, vertically, or both.