import pkg_resources as res

REF_CODE = ord('R')


def get_glyphs(font_variant: str):
    res_path = 'data/hershey-fonts/{}.jhf'.format(font_variant.strip())
    if not res.resource_exists(__package__, res_path):
        raise ValueError('Invalid Hershey font name: {!r}'.format(font_variant))
    # Open font file
    f = res.resource_string(__package__, res_path)
    # Decode strings as ascii
    strings = bytes(f).decode('ascii')
    # Parse it line by line
    glyphs = []
    for l in strings.splitlines():
        strokes = []
        if len(l) < 1:
            raise RuntimeError('Invalid line in Hershey font file: {}'.format(l))
        lpos = ord(l[8]) - REF_CODE
        rpos = ord(l[9]) - REF_CODE
        vert = l[10:]
        vert = vert.split(' R')
        vert = [[v[i:i+2] for i in range(0, len(v), 2)] for v in vert]
        # Now vert is a list of lists of 2-character sequences that represent coordinates:
        # [
        #   ['AB', 'WH', 'YZ'],
        #   ['RY', 'OY'],
        #   ['MX', 'MY', 'EY'],
        #   ...
        # ]
        # Each two-letter group represents X,Y coords of a point.
        # We move into a sequence with PU, go across all the points of a group with PD, and then do PU.
        for path in vert:
            points = []
            for twoch in path:
                x = ord(twoch[0]) - REF_CODE
                y = ord(twoch[1]) - REF_CODE
                points.append((x, y))
            strokes.append(points)
        glyphs.append((lpos, rpos, strokes))
    return glyphs

