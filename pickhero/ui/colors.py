"""Color constants for PickHero UI.

Rocksmith-style string palette. Supports dark/light themes via Theme dataclass.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """All UI colors for a theme."""

    # Background
    bg: tuple[int, int, int]

    # Lane backgrounds (alternating)
    lane_bg_even: tuple[int, int, int]
    lane_bg_odd: tuple[int, int, int]
    lane_line: tuple[int, int, int]

    # Hit zone
    hit_zone: tuple[int, int, int]

    # Note text and border
    note_text: tuple[int, int, int]
    note_border: tuple[int, int, int]

    # Menu
    menu_bg: tuple[int, int, int]
    menu_item: tuple[int, int, int]
    menu_selected: tuple[int, int, int]
    menu_selected_bg: tuple[int, int, int]
    menu_check: tuple[int, int, int]

    # HUD
    hud_text: tuple[int, int, int]
    hud_accent: tuple[int, int, int]

    # Feedback
    feedback_hit: tuple[int, int, int]
    feedback_close: tuple[int, int, int]
    feedback_miss: tuple[int, int, int]
    feedback_streak: tuple[int, int, int]

    # Loop markers
    loop_marker: tuple[int, int, int]
    loop_marker_disabled: tuple[int, int, int]
    loop_region: tuple[int, int, int, int]          # RGBA
    loop_region_disabled: tuple[int, int, int, int]  # RGBA

    # Signal meter
    signal_hot: tuple[int, int, int]
    signal_warm: tuple[int, int, int]
    signal_cold: tuple[int, int, int]

    # Tuner
    tuner_in_tune: tuple[int, int, int]
    tuner_close: tuple[int, int, int]
    tuner_off: tuple[int, int, int]


DARK_THEME = Theme(
    # The board is an OBJECT lying on a background, and it only reads as one
    # if the two differ. They were ten points apart -- near-black on
    # near-black -- so the board dissolved into the screen and the notes
    # floated. The board is dark warm wood now and the surround a cool grey,
    # which is the same relationship the reference uses (dark fretboard,
    # bright surround) at the brightness a dark theme is chosen for.
    bg=(38, 42, 58),
    lane_bg_even=(26, 23, 22),
    lane_bg_odd=(23, 20, 19),
    lane_line=(60, 60, 80),
    hit_zone=(255, 255, 255),
    note_text=(255, 255, 255),
    note_border=(10, 10, 15),
    menu_bg=(20, 20, 30),
    menu_item=(180, 180, 200),
    menu_selected=(255, 255, 255),
    menu_selected_bg=(60, 60, 100),
    menu_check=(50, 220, 80),
    hud_text=(200, 200, 220),
    hud_accent=(100, 180, 255),
    feedback_hit=(120, 255, 140),
    feedback_close=(255, 240, 130),
    feedback_miss=(255, 105, 115),
    feedback_streak=(255, 180, 50),
    loop_marker=(0, 200, 255),
    loop_marker_disabled=(0, 80, 110),
    loop_region=(0, 200, 255, 25),
    loop_region_disabled=(0, 80, 110, 15),
    signal_hot=(50, 220, 80),
    signal_warm=(220, 200, 40),
    signal_cold=(70, 70, 90),
    tuner_in_tune=(50, 220, 80),
    tuner_close=(220, 200, 40),
    tuner_off=(220, 100, 40),
)

LIGHT_THEME = Theme(
    bg=(235, 235, 240),
    lane_bg_even=(225, 225, 232),
    lane_bg_odd=(218, 218, 228),
    lane_line=(180, 180, 195),
    hit_zone=(40, 40, 50),
    note_text=(255, 255, 255),
    note_border=(80, 80, 100),
    menu_bg=(235, 235, 240),
    menu_item=(80, 80, 100),
    menu_selected=(20, 20, 30),
    menu_selected_bg=(180, 200, 240),
    menu_check=(30, 160, 60),
    hud_text=(60, 60, 80),
    hud_accent=(30, 100, 200),
    feedback_hit=(60, 190, 80),
    feedback_close=(215, 185, 45),
    feedback_miss=(230, 70, 85),
    feedback_streak=(200, 140, 30),
    loop_marker=(0, 150, 200),
    loop_marker_disabled=(100, 140, 160),
    loop_region=(0, 150, 200, 30),
    loop_region_disabled=(100, 140, 160, 15),
    signal_hot=(30, 180, 60),
    signal_warm=(200, 170, 20),
    signal_cold=(160, 160, 175),
    tuner_in_tune=(30, 180, 60),
    tuner_close=(200, 170, 20),
    tuner_off=(200, 80, 30),
)

_THEMES = {"dark": DARK_THEME, "light": LIGHT_THEME}
_current_theme: Theme = DARK_THEME


def set_theme(name: str) -> None:
    """Set the active theme by name ('dark' or 'light')."""
    global _current_theme
    _current_theme = _THEMES.get(name, DARK_THEME)


def get_theme() -> Theme:
    """Return the active theme."""
    return _current_theme


def get_theme_name() -> str:
    """Return the name of the active theme."""
    if _current_theme is LIGHT_THEME:
        return "light"
    return "dark"


def cycle_theme() -> str:
    """Cycle to the next theme. Returns the new theme name."""
    if _current_theme is DARK_THEME:
        set_theme("light")
        return "light"
    set_theme("dark")
    return "dark"


# String colors (Rocksmith palette, keyed 1-6: 1=high E, 6=low E)
# These don't change with theme — they're gameplay identifiers.
#
# Two palettes share this screen and they must not be confusable: a string
# colour says WHICH STRING, a feedback colour says HOW IT WENT. The plain
# Rocksmith palette collided with all three feedback colours at once -- its
# green sat on top of "correct", its red on "missed", its yellow on "close" --
# so a note on the A string looked like a note played right. These are pulled
# off those three hues: A is teal rather than green, high E leans crimson
# rather than pure red, B leans amber. The feedback colours in turn are far
# brighter and lighter than any string, so the two read as different kinds of
# colour even where the hue is nearest.
# Sampled from the reference the player reads without thinking, then checked
# against the rule this file exists for. Four of these are that palette's own
# values; its RED is deliberately NOT here, because at (248, 98, 98) it is
# within a few points of `feedback_miss` -- a string that looks like a missed
# note is exactly the collision the separation below is about. The two
# replacements sit in the same family: high saturation, high value, and out of
# the green and red bands the feedback colours own.
#
# Ordered so that NEIGHBOURING lanes never share a hue family: the lane above
# is the one a note can be confused with.
STRING_COLORS: dict[int, tuple[int, int, int]] = {
    1: (200, 32, 255),   # magenta
    2: (247, 169, 6),    # amber
    3: (0, 175, 254),    # cyan
    4: (124, 104, 250),  # violet
    5: (0, 195, 175),    # teal
    6: (62, 106, 224),   # blue
}

# An open string is grey, whichever string it is. The lane already says WHICH
# string -- that is what the six lanes are for -- so the colour is free to say
# something the position cannot, and "nothing to fret" is the most useful
# thing it can say. It is also what makes a chord read at a glance: the open
# strings drop back and the shape the hand has to make stands out.
#
# It is not a feedback colour and not a string colour, so it cannot be
# confused with either -- the separation the rest of this file is about.
OPEN_STRING_COLOR: tuple[int, int, int] = (150, 155, 165)


def dimmed(color: tuple[int, int, int], factor: float = 0.4) -> tuple[int, int, int]:
    """Darken a color by multiplying each channel by factor."""
    return (
        int(color[0] * factor),
        int(color[1] * factor),
        int(color[2] * factor),
    )


def lightened(color: tuple[int, int, int], amount: float = 0.4) -> tuple[int, int, int]:
    """Blend a color toward white. Works on dark and light themes alike,
    where multiplying by a factor > 1 would clip on already-bright colors."""
    return tuple(int(c + (255 - c) * amount) for c in color)
