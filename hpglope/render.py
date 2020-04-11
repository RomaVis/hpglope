import logging
import cairo
import re
import math
import os
from enum import IntEnum
from typing import Sequence
from hpglope.fonts import get_font_by_name
from collections import namedtuple


logger = logging.getLogger(__name__)


PenConfig = namedtuple('PenConfig', ('color', 'line_width'))


class RenderException(RuntimeError):
    pass


class RenderImageFormat(IntEnum):
    PNG = 0
    PDF = 1


class RenderConfig:
    DEFAULT = {
        # Paper size in mm (width x height)
        'paper': [297, 210],
        # How much mm to strip away from the image (for printable PDF recommended to set to 0).
        # Values order: [top, left, bottom, right].
        'crop': [25, 10, 5, 15],
        #'paper': [420, 297],
        # DPI specification. This determines raster image size. For PDF this does not matter.
        'dpi': 400,
        # Background color.
        'background_color': '#000000',
        # Pens.
        'pens' : {
            '1': {
                'color': '#00FA9A',
                'line_width': 0.3
            },
            '2': {
                'color': '#1E90FF',
                'line_width': 0.5
            },
            '3': {
                'color': '#7B68EE',
                'line_width': 0.5
            },
            '4': {
                'color': '#F5F5DC',
                'line_width': 0.5
            },
            '5': {
                'color': '#DB7093',
                'line_width': 0.5
            }
        },
        # Text rendering options
        'text': {
            # Font
            'font': 'hershey:rowmans',
            # Line width (optional)
            'line_width': 0.5,
            # Color (optional)
            'color': '#EB9605'
        }
    }

    @staticmethod
    def parse_color(spec):
        if isinstance(spec, str):
            # '#rrggbb' expected
            spec = spec.lstrip().lstrip('#')
            spec = int(spec, 16)
        if isinstance(spec, int):
            r = (spec >> 16) & 0xFF
            g = (spec >> 8) & 0xFF
            b = spec & 0xFF
            c = (r / 255, g / 255, b / 255, 1.0)
        elif isinstance(spec, Sequence):
            c = spec[:4]
        else:
            raise ValueError('Invalid color specification: {!r}'.format(spec))
        return c

    def __init__(self, conf: dict = None):
        if conf is None:
            conf = self.DEFAULT
        # Paper size
        self.paper_w = float(conf['paper'][0])
        self.paper_h = float(conf['paper'][1])
        # Crop margins
        self.crop_t = 0
        self.crop_l = 0
        self.crop_b = 0
        self.crop_r = 0
        if conf.get('crop') is not None:
            self.crop_t = float(conf['crop'][0])
            self.crop_l = float(conf['crop'][1])
            self.crop_b = float(conf['crop'][2])
            self.crop_r = float(conf['crop'][3])
        # DPI setting
        self.dpi = float(conf['dpi'])
        # Colors
        self.color_bg = self.parse_color(conf['background_color'])
        self.pens = {
            0: PenConfig(color=(0,0,0,0), line_width=0)
        }
        for k in conf['pens']:
            k = k.strip()
            if re.match(r'[0-9]+', k):
                self.pens[int(k)] = PenConfig(
                    color=self.parse_color(conf['pens'][k]['color']),
                    line_width=float(conf['pens'][k]['line_width']),
                )
        # Text
        self.text_font = get_font_by_name(conf['text']['font'])
        self.text_line_width = conf['text'].get('line_width', None)
        if self.text_line_width is not None:
            self.text_line_width = float(self.text_line_width)
        self.text_color = conf['text'].get('color', None)
        if self.text_color is not None:
            self.text_color = self.parse_color(self.text_color)


class HpglRenderer:
    HPGL_UNIT = 0.025  # mm
    HPGL_DEFAULT_CHAR_W = 0.285 * 10  # mm
    HPGL_DEFAULT_CHAR_H = 0.375 * 10  # mm
    HPGL_CHAR_STEP_X = 1.5  # in character width units
    HPGL_CHAR_STEP_Y = 2.0  # in character height units

    def __init__(self, config: RenderConfig):
        #
        # A note on coordinate system.
        # Cairo draws onto ImageSurface which uses mm units.
        # We map plotter units (HPGL_UNIT) to mm using an always-active transformation matrix (it's part of Cairo
        # context).
        # However, for all operations that involve user coordinates, we keep a separate transformation matrix that maps
        # those coordinates onto absolute HPGL coordinates.
        #
        self.config = config
        # We draw everything onto RecordingSurface which uses mm units. This can be used later to save drawing to any
        # file format.
        # Take into account margins specified by config
        # self.draw_w = config.paper_w - config.crop_l - config.crop_r
        # self.draw_h = config.paper_h - config.crop_t - config.crop_b
        self.surface = cairo.RecordingSurface(
            cairo.CONTENT_COLOR_ALPHA,
            cairo.Rectangle(
                config.crop_l,
                config.crop_t,
                config.paper_w - config.crop_l - config.crop_r,
                config.paper_h - config.crop_t - config.crop_b
            )
        )
        # PyCairo context
        self.ctx = cairo.Context(self.surface)
        # Fill background of canvas
        self.ctx.set_source_rgba(*config.color_bg)
        self.ctx.paint()
        # Define the rest of instance attributes and then call reset() to set them up.
        # self.abs_x_min = 0
        # self.abs_x_max = 0
        # self.abs_y_min = 0
        # self.abs_y_max = 0
        self.rot = 0
        # Default user coordinate mapping
        self.p1_abs = (0, 0)
        self.p2_abs = (0, 0)
        self.p1_usr = self.p1_abs
        self.p2_usr = self.p2_abs
        # Font params (in HPGL units)
        self.char_w = 0
        self.char_h = 0
        self.char_tilt_tg = 0
        self.trans_user_to_hpgl = cairo.Matrix()
        self.trans_char_to_hpgl = cairo.Matrix()
        self.pen_down = False
        # Initialize HPGL params.
        self.reset()

    def reset(self):
        # Plotter absolute coordinates
        # self.abs_x_min = 0
        # self.abs_x_max = self.config.paper_w / self.HPGL_UNIT
        # self.abs_y_min = 0
        # self.abs_y_max = self.config.paper_h / self.HPGL_UNIT
        self.rot = 0
        # Default user coordinate mapping
        self.p1_abs = (0, 0)
        self.p2_abs = (self.config.paper_w * self.HPGL_UNIT, self.config.paper_h * self.HPGL_UNIT)
        self.p1_usr = self.p1_abs
        self.p2_usr = self.p2_abs
        # Font params (in HPGL units)
        self.char_w = self.HPGL_DEFAULT_CHAR_W / self.HPGL_UNIT
        self.char_h = self.HPGL_DEFAULT_CHAR_H / self.HPGL_UNIT
        self.char_tilt_tg = 0  # tan(Theta) for character tilt.
        # Init plotter coordinate system.
        self.init_absolute_coordinates()
        # Compute user -> absolute coordinates transformation matrix
        self.update_user_coordinate_transform()
        # Font transformation matrix (from character coordinates to HPGL absolute)
        self.update_char_coordinate_transform()
        # Reset context
        self.ctx.reset_clip()
        self.ctx.new_path()
        # Set starting point
        self.ctx.move_to(0,0)
        # Default pen - no pen
        self.choose_pen(0)
        # Line style
        self.ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        self.ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        # Pen up
        self.pen_down = False

    def init_absolute_coordinates(self):
        # Set transformation matrix for HPGL absolute coordinates to image coordinates
        self.ctx.identity_matrix()
        # Move the origin correctly according to selected rotation
        if self.rot == 1:
            # 90 deg
            self.ctx.translate(self.config.paper_w, self.config.paper_h)
            self.ctx.scale(1, -1)
            self.ctx.rotate(math.pi / 2)
        elif self.rot == 2:
            # 180 deg
            self.ctx.translate(self.config.paper_w, 0)
            self.ctx.scale(1, -1)
            self.ctx.rotate(math.pi)
        elif self.rot == 3:
            # 270 deg
            self.ctx.scale(1, -1)
            self.ctx.rotate(math.pi * 3 / 2)
        else:
            # 0 deg
            self.ctx.translate(0, self.config.paper_h)
            self.ctx.scale(1, -1)
        # Transformation: abs plotter coords in HW_UNIT units (p1/p2) -> surface coords (img_w, img_h)
        # kx = self.HPGL_UNIT
        # ky = self.HPGL_UNIT
        # bx = -kx * 0
        # by = -ky * 0
        # self.ctx.translate(bx, by)
        self.ctx.scale(self.HPGL_UNIT, self.HPGL_UNIT)

    def update_user_coordinate_transform(self):
        # Transformation: usr coords (p1/p2) ->  abs coords (p1/p2)
        kx = (self.p2_abs[0] - self.p1_abs[0]) / (self.p2_usr[0] - self.p1_usr[0])
        ky = (self.p2_abs[1] - self.p1_abs[1]) / (self.p2_usr[1] - self.p1_usr[1])
        bx = self.p1_abs[0] - kx * self.p1_usr[0]
        by = self.p1_abs[1] - ky * self.p1_usr[1]
        self.trans_user_to_hpgl = cairo.Matrix(xx=kx, yy=ky, x0=bx, y0=by)

    def update_char_coordinate_transform(self):
        mat_slant = cairo.Matrix(xx=1, yy=1, xy=self.char_tilt_tg)
        mat_scale = cairo.Matrix(xx=self.char_w, yy=self.char_h)
        self.trans_char_to_hpgl = mat_scale.multiply(mat_slant)

    def choose_pen(self, pen):
        pencfg = self.config.pens.get(pen, self.config.pens[0])
        self.ctx.set_source_rgba(*pencfg.color)
        self.ctx.set_line_width(pencfg.line_width / self.HPGL_UNIT)

    def ip(self, x1, y1, x2, y2):
        self.p1_abs = [x1, y1]
        self.p2_abs = [x2, y2]

    def sc(self, xmin, xmax, ymin, ymax):
        self.p1_usr = [xmin, ymin]
        self.p2_usr = [xmax, ymax]
        # Update transformation
        self.update_user_coordinate_transform()

    def ro(self, angle:int):
        if angle == 90:
            self.rot = 1
        elif angle == 90:
            self.rot = 2
        elif angle == 90:
            self.rot = 3
        else:
            self.rot = 0
        self.init_absolute_coordinates()

    def sc_reset(self):
        self.p1_usr = self.p1_abs
        self.p2_usr = self.p2_abs
        self.update_user_coordinate_transform()

    def iw(self, x1, y1, x2, y2):
        # TODO
        pass

    def iw_cancel(self):
        # TODO
        pass

    def si(self, width_cm, height_cm):
        self.char_w = width_cm * 10 / self.HPGL_UNIT
        self.char_h = height_cm * 10 / self.HPGL_UNIT
        self.update_char_coordinate_transform()

    def su(self, width_usr, height_usr):
        self.char_w = self.trans_user_to_hpgl.transform_distance(width_usr, 0)
        self.char_h = self.trans_user_to_hpgl.transform_distance(0, height_usr)
        self.update_char_coordinate_transform()

    def sr(self, perc_width, perc_height):
        self.char_w = perc_width * (self.p2_abs[0] - self.p1_abs[0]) * 0.01
        self.char_h = perc_height * (self.p2_abs[1] - self.p1_abs[1]) * 0.01
        self.update_char_coordinate_transform()

    def sl(self, tangent_theta):
        self.char_tilt_tg = tangent_theta
        self.update_char_coordinate_transform()

    def sp(self, pen):
        self.choose_pen(pen)

    def raw_pen_down(self):
        self.pen_down = True

    def raw_pen_up(self):
        if self.pen_down:
            # Render
            cur = self.ctx.get_current_point()
            self.ctx.stroke()
            self.ctx.move_to(*cur)
        self.pen_down = False

    def raw_move(self, points):
        if self.pen_down:
            for x, y in points:
                self.ctx.line_to(x, y)
        else:
            for x, y in points:
                self.ctx.move_to(x, y)

    def pa(self, points):
        self.raw_move((self.trans_user_to_hpgl.transform_point(x, y) for x, y in points))

    def pu(self, points=()):
        self.raw_pen_up()
        self.pa(points)

    def pd(self, points=()):
        self.raw_pen_down()
        self.pa(points)

    def lb(self, text):
        org_x, org_y = self.ctx.get_current_point()
        char_org_x, char_org_y = org_x, org_y
        # Apply text-only settings
        self.ctx.save()
        if self.config.text_line_width is not None:
            self.ctx.set_line_width(self.config.text_line_width / self.HPGL_UNIT)
        if self.config.text_color is not None:
            self.ctx.set_source_rgba(*self.config.text_color)
        for c in text:
            if c == '\n':
                # LF and CR?
                char_org_y -= self.char_h * self.HPGL_CHAR_STEP_Y
                char_org_x = org_x
            elif c == '\r':
                # Just a CR
                char_org_x = org_x
            else:
                strokes = self.config.text_font.get_paths(c)
                if strokes:
                    for pd, cx, cy in strokes:
                        px, py = self.trans_char_to_hpgl.transform_point(cx, cy)
                        px += char_org_x
                        py += char_org_y
                        if pd:
                            self.raw_pen_down()
                        else:
                            self.raw_pen_up()
                        self.raw_move(((px, py),))
                char_org_x += self.char_w * self.HPGL_CHAR_STEP_X
            self.raw_pen_up()
            self.raw_move(((char_org_x, char_org_y),))
        # Restore normal settings
        self.ctx.restore()

    def save(self, filename: str, file_format):
        logger.info('Saving drawing into {!r}'.format(filename))
        # The size of our paper surface
        draw_w = self.surface.get_extents().width
        draw_h = self.surface.get_extents().height
        if file_format == RenderImageFormat.PNG:
            dot_per_mm = self.config.dpi / 25.4
            img_w = draw_w * dot_per_mm
            img_h = draw_h * dot_per_mm
            img_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(img_w), int(img_h))
        elif file_format == RenderImageFormat.PDF:
            img_w = draw_w * 72 / 25.4
            img_h = draw_h * 72 / 25.4
            img_surface = cairo.PDFSurface(filename, int(img_w), int(img_h))
        else:
            raise ValueError('Unknown file format: {!r}'.format(file_format))
        img_ctx = cairo.Context(img_surface)
        img_ctx.identity_matrix()
        # self.surface has been drawn in paper (mm) coordinates. We need to remap them to image coordinates.
        # Paper coords also have origin in the top-left corner, Y axis going down and X right. So we only need to do a
        # rescaling.
        img_ctx.scale(img_w / draw_w, img_h / draw_h)
        # Move paper surface so that we see only a cropped area here.
        img_ctx.translate(-self.surface.get_extents().x, -self.surface.get_extents().y)
        # Paint onto our image
        img_ctx.set_source_surface(self.surface)
        img_ctx.paint()
        # PNG requires a separate write() call
        if isinstance(img_surface, cairo.ImageSurface):
            img_surface.write_to_png(filename)

    def process_command(self, cmd: str):
        cmd_type = cmd[:2].upper()
        cmd_arg_str = cmd[2:]
        if not cmd_arg_str.strip():
            cmd_args = []
        else:
            cmd_args = cmd_arg_str.strip().split(',')
        if cmd_type == 'IN':
            # Initialize plotter.
            self.reset()
        elif cmd_type == 'DF':
            # Set plotter to default.
            self.reset()
        elif cmd_type == 'DT':
            # DT command - defines a new special terminator symbol
            # Here we ignore it - should be handled in parser
            pass
        elif cmd_type == 'IP':
            # Input P1,P2
            flts = [float(a.strip()) for a in cmd_args]
            if len(flts) != 4:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.ip(flts[0], flts[1], flts[2], flts[3])
        elif cmd_type == 'SC':
            # Scale
            flts = [float(a.strip()) for a in cmd_args]
            if len(flts) != 4:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.sc(flts[0], flts[1], flts[2], flts[3])
        elif cmd_type == 'RO':
            # Rotate absolute coords 0 or 90 degrees
            vals = [int(a.strip()) for a in cmd_args]
            if not vals:
                self.ro(0)
            elif len(vals) == 1:
                angle = vals[0]
                self.ro(angle)
            else:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
        elif cmd_type == 'IW':
            # Bounding rect
            vals = [float(a.strip()) for a in cmd_args]
            if len(vals) == 0:
                self.iw_cancel()
            elif len(vals) == 4:
                self.iw(vals[0], vals[1], vals[2], vals[3])
            else:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
        elif cmd_type == 'SR':
            # Character size
            flts = [float(a.strip()) for a in cmd_args]
            if len(flts) != 2:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.sr(flts[0], flts[1])
        elif cmd_type == 'SP':
            # Select pen
            vals = [int(a.strip()) for a in cmd_args]
            if len(vals) != 1:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.sp(vals[0])
        elif cmd_type == 'SL':
            # Character slant
            vals = [float(a.strip()) for a in cmd_args]
            if len(vals) == 0:
                self.sl(0)
            elif len(vals) == 1:
                self.sl(vals[0])
            else:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
        elif cmd_type == 'PU':
            # Pen up
            vals = [float(a.strip()) for a in cmd_args]
            if len(vals) & 0x1:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.pu(list(zip(vals[0::2], vals[1::2])))
        elif cmd_type == 'PD':
            # Pen down
            vals = [float(a.strip()) for a in cmd_args]
            if len(vals) & 0x1:
                raise RenderException('Invalid command: {!r}.'.format(cmd))
            self.pd(list(zip(vals[0::2], vals[1::2])))
        elif cmd_type == 'LB':
            # Label
            self.lb(cmd_arg_str)
        else:
            raise RenderException('Unknown command: {!r}.'.format(cmd))


def main():
    logging.basicConfig(level=logging.DEBUG)
    c = HpglRenderer(RenderConfig())
    c.sp(1)
    c.pu([(500, 4000)])
    c.pd([(5000, 4000)])
    c.pu([(500, 4000)])
    c.si(1.0, 1.8)
    c.sl(0.2)
    c.lb('Hello,\nworld!')
    c.save('test.png', RenderImageFormat.PNG)
    c.save('test.pdf', RenderImageFormat.PDF)


if __name__ == '__main__':
    main()
