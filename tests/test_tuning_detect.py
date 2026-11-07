"""Which tuning is the guitar in?

Three things are being held here and they fail in different ways:

- the NARROWING, which must ask only strings that decide and must stop as
  soon as the answer cannot change;
- the FOLD, which is the whole reason this is a guided query rather than a
  listener -- the low E of a guitar comes back from yinfast as a third of
  its pitch, and multiplying it back up is only safe once the candidates
  have narrowed enough that no two of them sit a fifth apart on the string
  being asked about;
- the REFUSALS, because a reading placed on the wrong pitch sets the wrong
  targets for every string after it.
"""

import pytest

from pickhero.audio.note_utils import NAMED_TUNINGS, midi_to_freq, midi_to_name
from pickhero.audio.tuning_detect import (
    ASK_ORDER, CONFIRM_READINGS, OUT_OF_TUNE_CENTS, Detection, candidates_for,
    fifth_pair, interpret, next_string, pitches_on,
)

#: Standard with only the low E dropped four semitones -- in NAMED_TUNINGS
#: nowhere, and every string still inside the range that string takes
#: across the named tunings, which is what the detector can reach.
MIXED = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 36}


def _run(shape, fold=1, readings=CONFIRM_READINGS):
    """Play the whole query as a perfect player, and say what was asked."""
    detect = Detection()
    asked = []
    guard = 0
    while not detect.finished:
        guard += 1
        assert guard < 20, f"the query did not end: {asked}"
        string = detect.asking
        asked.append(string)
        for _ in range(readings):
            detect.offer(midi_to_freq(shape[string]) / fold)
        if detect.asking == string:
            return asked, detect, False
    return asked, detect, True


class TestEveryNamedTuningIsFound:
    def test_all_sixteen(self):
        for name, shape in NAMED_TUNINGS:
            asked, detect, got_there = _run(shape)
            assert got_there, f"{name} stalled on string {asked[-1]}"
            answer = detect.answer()
            assert answer is not None and answer[0] == name

    def test_four_questions_at_worst(self):
        """Measured: 2.25 on average, 4 at worst (Drop D against DADGAD,
        which differ only on strings 2 and 1)."""
        counts = [len(_run(shape)[0]) for _, shape in NAMED_TUNINGS]
        assert max(counts) <= 4
        assert sum(counts) / len(counts) < 2.5

    def test_a_string_they_all_agree_on_is_never_asked(self):
        """Standard needs strings 3 and 6 and nothing else: once those two
        are known no other string can change the answer."""
        asked, _, _ = _run(dict(NAMED_TUNINGS[0][1]))
        assert asked == [3, 6]

    def test_it_stops_only_when_the_answer_cannot_change(self):
        """`next_string` returning None with two candidates still standing
        would be the query giving up with the answer undecided. It cannot
        happen -- no two named tunings are the same shape -- and that is
        asserted rather than trusted, over every prefix of every tuning."""
        for _, shape in NAMED_TUNINGS:
            for length in range(len(ASK_ORDER) + 1):
                asked = set(ASK_ORDER[:length])
                heard = {s: shape[s] for s in asked}
                left = candidates_for(heard)
                if len(left) >= 2:
                    assert next_string(left, asked) is not None


class TestTheFoldIsSafeBecauseItAsksACleanStringFirst:
    def test_which_strings_a_fold_cannot_separate(self):
        """Two candidate pitches a fifth apart are indistinguishable to a
        fold: half of A is, to two cents, a third of A+7. Over the full
        sixteen that is strings 6, 5 and 4; on 3, 2 and 1 the pitches span
        under a fifth, so it is impossible."""
        pairs = {s: fifth_pair(pitches_on(s, NAMED_TUNINGS))
                 for s in ASK_ORDER}
        assert pairs[6] == (33, 40)
        assert pairs[5] == (40, 47)
        assert pairs[4] == (45, 52)
        assert pairs[3] is None and pairs[2] is None and pairs[1] is None

    def test_no_question_is_ever_one_a_fold_cannot_answer(self):
        """The opening question has no narrowing behind it, so the string it
        asks for has to be clean on its own. Asked low-string-first without
        this, all sixteen tunings open on a question whose targets carry the
        (33, 40) pair -- and that is the commonest tuning there is reading
        as Drop A."""
        for name, shape in NAMED_TUNINGS:
            detect = Detection()
            while not detect.finished:
                string = detect.asking
                assert fifth_pair(detect.targets) is None, (
                    f"{name}: string {string} targets {detect.targets}")
                detect.heard[string] = shape[string]
                detect.asked.add(string)

    def test_every_tuning_survives_every_reading_being_a_third_of_itself(self):
        """Which is what the low E really does -- 301 of 301 confident
        readings on a clean take."""
        for name, shape in NAMED_TUNINGS:
            asked, detect, got_there = _run(shape, fold=3)
            assert got_there, f"{name} stalled on string {asked[-1]}"
            answer = detect.answer()
            assert answer is not None and answer[0] == name

    def test_and_an_ordinary_octave_error_too(self):
        for name, shape in NAMED_TUNINGS:
            _, detect, got_there = _run(shape, fold=2)
            assert got_there
            assert detect.answer()[0] == name

    def test_the_cents_survive_the_fold_exactly(self):
        """Nothing is estimated: if the detector found the period of three
        cycles then 1200*log2(3f/target) is the fundamental's cents."""
        targets = [38, 40]
        wanted = midi_to_freq(40) * 2 ** (-0.18 / 12)
        found = interpret(wanted / 3.0, targets)
        assert found.midi == 40
        assert found.multiple == 3
        assert found.cents == pytest.approx(-18.0, abs=0.01)


class TestItRefusesRatherThanGuessing:
    def test_a_fifth_pair_is_refused_from_both_sides(self):
        """E2 over three and A1 over two are the same reading. Ranking the
        multiples against each other answers Standard for one and Drop A
        for the other, and both answers are wrong half the time."""
        targets = pitches_on(6, NAMED_TUNINGS)
        for midi, fold in ((40, 3.0), (33, 2.0)):
            found = interpret(midi_to_freq(midi) / fold, targets)
            assert found.midi is None
            assert "A1" in found.why and "E2" in found.why

    def test_half_a_semitone_out_is_placed_nowhere(self):
        """Beyond half a semitone nothing is that note mistuned, it is a
        different note -- and the refusal names the frequency AND what it
        would be chromatically, so the player can see what was heard
        without the detector claiming a tuning."""
        targets = pitches_on(6, NAMED_TUNINGS)
        found = interpret(midi_to_freq(40) * 2 ** (0.6 / 12), targets)
        assert found.midi is None
        assert "85 Hz" in found.why and "F2" in found.why

    def test_a_pitch_nothing_is_near_says_so(self):
        found = interpret(1000.0, pitches_on(6, NAMED_TUNINGS))
        assert found.midi is None and "1000 Hz" in found.why

    def test_a_refusal_never_narrows_anything(self):
        detect = Detection()
        string = detect.asking
        for _ in range(CONFIRM_READINGS * 3):
            detect.offer(1000.0)
        assert detect.heard == {} and detect.asking == string

    def test_nothing_to_compare_it_with(self):
        assert interpret(110.0, []).midi is None
        assert interpret(-1.0, [40]).midi is None


class TestFiveAgreeingReadingsUnbroken:
    def test_four_is_not_enough(self):
        detect = Detection()
        string = detect.asking
        pitch = midi_to_freq(NAMED_TUNINGS[0][1][string])
        for _ in range(CONFIRM_READINGS - 1):
            detect.offer(pitch)
        assert detect.heard == {}
        detect.offer(pitch)
        assert detect.heard == {string: NAMED_TUNINGS[0][1][string]}

    def test_a_disagreeing_reading_resets_the_run(self):
        """A single frame can be a harmonic or a neighbour still ringing,
        and narrowing on one sets the wrong targets for every string after
        it -- so the run is unbroken, not a tally."""
        detect = Detection()
        string = detect.asking
        shape = NAMED_TUNINGS[0][1]
        right = midi_to_freq(shape[string])
        other = midi_to_freq(shape[string] + 1)
        for _ in range(CONFIRM_READINGS - 1):
            detect.offer(right)
        detect.offer(other)
        detect.offer(right)
        assert detect.heard == {}
        assert detect.confirmed == 1


class TestAStringThatWillNotRead:
    def test_skipping_moves_on(self):
        detect = Detection()
        first = detect.asking
        detect.skip()
        assert detect.asking is not None and detect.asking != first

    def test_skipping_the_deciding_string_is_said_rather_than_guessed(self):
        """Two tunings is a far better answer than whichever of them a
        guess would have landed on."""
        detect = Detection()
        shape = dict(NAMED_TUNINGS[1][1])          # Drop D
        for _ in range(len(ASK_ORDER) * 2):
            string = detect.asking
            if string is None:
                break
            if string in (2, 1):
                # The two strings Drop D and DADGAD differ on. Skipping
                # both is what leaves it genuinely undecided.
                detect.skip()
                continue
            for _ in range(CONFIRM_READINGS):
                detect.offer(midi_to_freq(shape[string]))
        assert detect.finished and detect.stalled
        assert detect.answer() is None
        possible = detect.still_possible()
        assert "Drop D" in possible and "DADGAD" in possible

    def test_a_skipped_string_never_comes_round_again(self):
        detect = Detection()
        skipped = set()
        for _ in range(len(ASK_ORDER) + 2):
            string = detect.asking
            if string is None:
                break
            assert string not in skipped
            skipped.add(string)
            detect.skip()
        assert detect.finished


class TestATuningNobodyNamed:
    """The narrowing is against the named list, so a guitar that is not in
    one of them cannot be narrowed TO anything. What it can still do is
    name the six notes, and that is the answer -- the letters are the fact
    and a name is a label, which is what `reachable_tunings` already does
    for the same reason."""

    def test_the_letters_are_the_answer(self):
        asked, detect, got_there = _run(MIXED)
        assert got_there
        assert detect.candidates == []
        answer = detect.answer()
        assert answer is not None
        assert answer[0] == "C A D G B E"
        assert answer[1] == MIXED

    def test_it_asks_for_all_six(self):
        """Nothing survives, so there is nothing left to narrow -- but the
        notes themselves are the whole answer here."""
        asked, _, _ = _run(MIXED)
        assert set(asked) == set(ASK_ORDER)

    def test_the_wider_tier_is_what_makes_it_reachable(self):
        """The reading that empties the candidate list is by definition
        outside every one of them, so the narrowed targets place it
        nowhere. Without the second tier the query simply stalls on that
        string and the player is told their guitar reads nothing."""
        detect = Detection()
        for _ in range(CONFIRM_READINGS):
            detect.offer(midi_to_freq(MIXED[3]))
        assert detect.asking == 6
        assert 36 not in detect.targets and 36 in detect.wider_targets

    def test_it_survives_the_fold_too(self):
        _, detect, got_there = _run(MIXED, fold=3)
        assert got_there and detect.answer()[0] == "C A D G B E"

    def test_still_possible_says_so(self):
        _, detect, _ = _run(MIXED)
        assert "no named tuning" in detect.still_possible()

    def test_a_string_outside_every_named_tuning_is_refused(self):
        """A seven-string tab's "pitched" tuning puts string 3 at A2, which
        no named six-string tuning uses there. Refused rather than placed
        on the nearest -- and the tuner's own LEFT/RIGHT and 1-6 are what
        that player reaches for instead."""
        detect = Detection()
        found = detect.offer(midi_to_freq(45))
        assert found.midi is None and detect.heard == {}


class TestAStringWellOutOfTune:
    def test_it_is_accepted_and_flagged(self):
        """Still plainly that note, which is why it is accepted -- and worth
        saying, because the next thing to do is tune rather than play."""
        detect = Detection()
        string = detect.asking
        midi = NAMED_TUNINGS[0][1][string]
        wanted = midi_to_freq(midi) * 2 ** (0.38 / 12)
        for _ in range(CONFIRM_READINGS):
            found = detect.offer(wanted)
        assert found.midi == midi and found.out_of_tune
        assert detect.cents[string] == pytest.approx(38.0, abs=0.5)

    def test_a_small_error_is_not_flagged(self):
        found = interpret(midi_to_freq(40) * 2 ** (0.1 / 12), [40])
        assert found.midi == 40 and not found.out_of_tune
        assert abs(found.cents) < OUT_OF_TUNE_CENTS


class TestStartingOver:
    def test_reset_forgets_everything(self):
        _, detect, _ = _run(dict(NAMED_TUNINGS[0][1]))
        detect.reset()
        assert detect.heard == {} and detect.asked == set()
        assert len(detect.candidates) == len(NAMED_TUNINGS)
        assert not detect.finished


class TestSayingWhichStringGaveWhat:
    def test_the_evidence_names_the_strings(self):
        """Two notes with nothing to say which strings they came off is an
        answer a player cannot check."""
        _, detect, _ = _run(dict(NAMED_TUNINGS[0][1]))
        assert detect.evidence() == "6: E, 3: G"
        assert detect.notes_heard() == "E G"

    def test_all_six_read_as_the_tuning_itself(self):
        _, detect, _ = _run(MIXED)
        assert detect.notes_heard() == "C A D G B E"
