# Reading-path sounds ride the speech stream

The reading path (`TextInfo.getControlFieldSpeech` — browse-mode reading, quicknav, say-all,
the Word caret) no longer plays every sound at hook time. The field the navigation just landed
**inside** still plays there — its utterance starts immediately, so the sound leads speech the
way the object events' do. Every other field's sound goes into the field's returned speech
sequence as a `speech.commands.CallbackCommand`; NVDA's speech manager converts it to a synth
index and fires it on the main thread when the synthesizer **reaches** the field. Position
extraction always stays in the hook, at build time, on the main thread; the callback closes over
`(slot, position)` and calls `play`, which returns in ~0.1 ms.

The split needs no utterance tracking, because `fieldType` already encodes it:
`start_addedToControlFieldStack` is a field the reading position landed inside, announced at the
head of an utterance that — under `CARET` and `QUICKNAV`, which cancel current speech — begins
now. `start_relative` is a field speech traverses mid-utterance. Under `SAYALL` even the
utterance start sits behind the read-ahead queue, so there everything rides. The predicate is
the lead/ride split inside `playback.decide` (originally `wiring.should_ride_speech`; relocated
verbatim by [ADR 0004](0004-the-playback-verdict-is-one-decide-call.md)), table-tested against
the #32 dataset: of its 113 plays, 84 lead and 29 ride.

The trigger was the first 2.0 smoke run (#52): a link's sound played seconds before say-all's
speech reached the link, and a table row with several controls fired every sound in one burst.
Both are the same fact — the hook runs when NVDA *composes* speech, and on the reading path
composition and utterance are decoupled: say-all queues lines ahead of the synth, and a
multi-control line builds all its fields in one pass. The object-event paths do not have this
problem, because a focus change cancels current speech and the freshly built utterance starts
immediately; they are unchanged, and their sounds still lead speech.

**This amends #10 decision 1.** "No syncing against the synth pipeline (it is unobservable from
our side)" was decided with the object-event model in mind, and its premise is false for the
reading path: the speech manager's index machinery is exactly that observation point —
`BaseCallbackCommand`s become `IndexCommand`s, `synthIndexReached` queues the handler onto the
main thread, and say-all's own read-ahead is built on it. The ordering contract becomes:
**sounds announcing where the user just arrived lead speech — object events and entered fields
alike; sounds announcing content speech is traversing ride it.**

## Considered options

**Keep playing at build time.** The shipped behaviour. Zero risk and the best possible quicknav
onset (~20 ms after keypress), but the smoke run showed what it buys that onset with: sounds
seconds early under say-all and bursts on multi-control lines — the sound announces the wrong
moment, which for a replacement of speech is wrongness, not lateness.

**Own timing: delay heuristics or a scheduler.** Estimate when speech will reach the field and
schedule the sound. Rejected without measurement: it reintroduces the timers #31 deleted, and it
guesses at a pipeline NVDA will simply tell us about.

**Callbacks for everything.** The first cut of this change, and NVDA's designed mechanism for
"when speech reaches here": no timers of ours, no polling, nothing on the hot path beyond
constructing one small command object; #31's rule that every sound is traceable to a synchronous
NVDA call still holds — the call is the manager's index handling instead of the hook. Rejected
on its first live trial: navigation in web browsers felt sluggish, because even the field the
user just jumped into waited out the synth's time-to-first-audio (~50–150 ms) where it used to
sound ~20 ms after the keypress.

**Hybrid: entered fields play immediately, callbacks for the rest (chosen).** Preserves the
sound-leads-speech onset where the sound answers a keypress, and rides speech where the sound
annotates content being read out. Initially dismissed on the belief that "first field of this
utterance" required utterance-boundary tracking the hook cannot see — wrongly: `fieldType`
already carries the distinction (above), so the split is a two-argument pure predicate.

## Consequences

- **Traversed-field sounds arrive with speech, not ahead of it** — coinciding with the element
  being spoken is that announcement doing its job. Entered fields keep the ~20 ms
  post-keypress onset on quicknav and caret navigation. The event→`play()` dispatch budget (§2)
  is untouched on every branch — for riding sounds, dispatch ends at command construction and
  the play itself is 0.09 ms at fire time.
- **Interrupting speech drops unspoken sounds.** Indexes from cancelled utterances are discarded
  by the manager, so content never spoken is never sounded — previously an interrupted say-all
  had already fired sounds for text the user never heard. #10 decision 5 is refined, not
  reversed: voices already in the air still ring out; what changes is that queued-but-unreached
  sounds die with the utterance.
- **A synth without `synthIndexReached` falls back to build-time play.** The manager waits
  forever for unreported indexes (say-all is equally broken on such a synth). Every in-tree
  synth reports; the check is two attribute reads against a value the 32-bit bridge caches.
- **Volume and device are read at fire time**, because `play` reads its settings provider when
  called — a mid-say-all volume change now applies to the sounds not yet reached.
- **Callback precision is the synth's index granularity** — tens of milliseconds, not
  sample-accurate. Fine for "the sound plays as speech reaches the link"; not a mechanism for
  tighter sync, should anyone ever want it.
