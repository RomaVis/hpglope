import re
from typing import Sequence, Tuple, Union
from abc import ABC, abstractmethod
# Fonts
from hpglope.fonts.stick_font import stick_font
from hpglope.fonts.hershey import get_glyphs as get_hershey_glyphs


class Font(ABC):
    @abstractmethod
    def get_paths(self, c: str) -> Union[None, Sequence[Tuple[bool, float, float]]]:
        """
        For given character c should return a sequence of strokes in the following form:
        (
            (true, 0.2, 0.3),   # PD to x=0.2 y=0.3
            (true, 0.3, 0.3),   # PD to x=0.2 y=0.3
            (false, 0.2, 0.5),  # PU to x=0.2 y=0.5
            ...
        )
        Coordinate system:
            - Origin (0, 0): usually in the left bottom corner of character, unless you want something else.
            - Scale: x and y range 0..1 corresponds to the character box.
             In this system, the typical HPGL horizontal offset between fixed-width characters is 1.5 and offset between lines is 2.0.
        :param c: character
        :return: sequence of strokes or None if such character is absent.
        """
        pass


def get_font_by_name(name: str) -> Font:
    if name == 'stick_font':
        return StickFont()
    elif name.startswith('hershey'):
        # Expected: 'hershey:variant'
        toks = name.split(':')
        if len(toks) < 2 or not toks[1]:
            raise ValueError('Invalid Hershey font name: {!r}'.format(name))
        return HersheyFont(toks[1])
    raise ValueError('Unknown font: {!r}'.format(name))


class StickFont(Font):
    def __init__(self):
        # Precompute strokes
        self.font = {}
        for c in stick_font:
            paths = stick_font[c]
            strokes = []
            for path in paths:
                for i, point in enumerate(path):
                    strokes.append((i != 0, point[0] / 4, point[1] / 8))
            self.font[c] = strokes

    def get_paths(self, c: str) -> Union[None, Sequence[Tuple[bool, float, float]]]:
        return self.font.get(c, None)


class HersheyFont(Font):
    def __init__(self, font_variant:str):
        # Precompute strokes
        glyphs = get_hershey_glyphs(font_variant)
        self.font = {}
        # Cap and bottomline assume normal font (NOTE: Y is inverted!)
        cap = -12
        bottom = 9
        # TODO: this is empirical. Hershey font is not monospace, but we assume it is.
        left = -6
        right = 7
        # Normalizing transformations
        ky = 1 / (cap - bottom)
        by = -ky * bottom
        kx = 1 / (right - left)
        bx = -kx * left
        for i, g in enumerate(glyphs):
            # TODO: this default Hershey mapping which is only for ASCII. Any non-ASCII charset / unicode will not work.
            c = chr(32 + i)
            gleft = g[0]
            gright = g[1]  # TODO: these are be needed for non-monospaced font rendering.
            paths = g[2]
            strokes = []
            for path in paths:
                for i, point in enumerate(path):
                    x = point[0]
                    y = point[1]
                    strokes.append((i != 0, x * kx + bx, y * ky + by))
            self.font[c] = strokes

    def get_paths(self, c: str) -> Union[None, Sequence[Tuple[bool, float, float]]]:
        return self.font.get(c, None)
