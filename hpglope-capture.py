import serial
import logging
import sys
import datetime
import argparse
import yaml
import os
from typing import Union
from hpglope.parser import HpglStreamParser, RenderImageFormat, RenderConfig

logger = logging.getLogger('hpglope-capture')


class CaptureConfig:
    PARITY_MAP = {
        'none': serial.PARITY_NONE,
        'even': serial.PARITY_EVEN,
        'odd': serial.PARITY_ODD
    }

    def __init__(self, config_dict: dict):
        # Img format
        self.img_format = str(config_dict['img']['format'])
        if self.img_format.lower() == 'png':
            self.img_format = RenderImageFormat.PNG
        elif self.img_format.lower() == 'pdf':
            self.img_format = RenderImageFormat.PDF
        else:
            raise ValueError('Unknown image format: {!r}'.format(self.img_format))
        # Img filename
        self.img_filename = str(config_dict['img']['filename'])
        # Dump filename
        self.dump_filename = config_dict.get('dump_filename', None)
        if self.dump_filename is not None:
            self.dump_filename = str(self.dump_filename)
        # Port
        self.port_name = str(config_dict['port']['name'])
        self.port_baud = int(config_dict['port']['baud'])
        self.port_parity = self.PARITY_MAP[str(config_dict['port']['parity']).lower()]
        self.port_rtscts = bool(config_dict['port']['rtscts'])
        self.port_dsrdtr = bool(config_dict['port']['dsrdtr'])
        self.port_xonxoff = bool(config_dict['port']['xonxoff'])


class Capture:
    def __init__(self, capture_config: CaptureConfig, canvas_config: RenderConfig):
        self.parser = HpglStreamParser(self.cmd_handler)
        self.config = capture_config
        self.canvas_config = canvas_config
        self.img_filename: Union[str, None] = None
        self.dump_filename: Union[str, None] = None

    def cmd_handler(self, cmd: str):
        if cmd.upper().startswith('IN'):
            # New plot
            ts = datetime.datetime.now()
            self.img_filename = ts.strftime(self.config.img_filename)
            self.dump_filename = ts.strftime(self.config.dump_filename) if self.config.dump_filename else None
            self.parser.start_plot(self.canvas_config)
        elif cmd.upper().startswith('DF'):
            # Finish plot
            self.parser.finish_plot(self.img_filename, self.config.img_format, self.dump_filename)

    def run(self):
        BLOCK_SIZE = 64
        logger.info('Starting HPGL capture.')
        with serial.Serial(self.config.port_name, baudrate=self.config.port_baud, parity=self.config.port_parity,
                           xonxoff=self.config.port_xonxoff, rtscts=self.config.port_rtscts,
                           dsrdtr=self.config.port_dsrdtr) as ser:
            # Clear whatever is there in the input buffer.
            ser.timeout = 0.1
            ser.readall()
            ser.timeout = None
            # Cycle - read commands
            try:
                ser.timeout = None
                while True:
                    if ser.timeout is None:
                        # No timeout: wait for the first byte
                        r = ser.read(1)
                        # Enable short timeout and read in blocks
                        ser.timeout = 0.1
                    else:
                        # Timeout is there: read in blocks
                        r = ser.read(BLOCK_SIZE)
                        if len(r) < BLOCK_SIZE:
                            # Timeouted, but not enough data. Resort to reading 1 byte at a time.
                            ser.timeout = None
                    # Skip null bytes
                    r = r.replace(b'\x00', b'')
                    self.parser.feed(r)
            except KeyboardInterrupt:
                logger.info('Exiting.')
                self.parser.finish_plot(self.img_filename, self.config.img_format, self.dump_filename)


def main():
    parser = argparse.ArgumentParser(
        description='Capture HPGL commands over UART, plot the image, and save it to the file.'
    )
    parser.add_argument(
        'capture_config',
        type=str,
        help='Path to capture driver YAML config file. Mandatory.'
    )
    parser.add_argument(
        'render_config',
        type=str,
        help='Path to renderer YAML config file. Mandatory.'
    )
    parser.add_argument(
        '--port',
        type=str,
        help='Serial port. If specified, will override setting from config file.'
    )
    parser.add_argument(
        '--dir', '-d',
        type=str,
        help='Output directory where files should be created. If not set, current working directory is used.'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose logging.'
    )
    args = parser.parse_args()

    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.INFO)
    capture_config_yml = args.capture_config
    render_config_yml = args.render_config
    port_name = args.port
    out_dir = args.dir

    # Load configs
    with open(capture_config_yml, 'r') as f:
        cfg_capture = CaptureConfig(yaml.safe_load(f.read()))
    with open(render_config_yml, 'r') as f:
        cfg_render = RenderConfig(yaml.safe_load(f.read()))

    # Override some config settings
    if port_name is not None:
        cfg_capture.port_name = port_name
    if out_dir is not None:
        if not os.path.isdir(out_dir):
            raise RuntimeError('Directory {!r} does not exist.'.format(out_dir))
        if not os.path.isabs(cfg_capture.img_filename):
            cfg_capture.img_filename = os.path.join(out_dir, cfg_capture.img_filename)
        if cfg_capture.dump_filename is not None and not os.path.isabs(cfg_capture.dump_filename):
            cfg_capture.dump_filename = os.path.join(out_dir, cfg_capture.dump_filename)

    # Run
    capture = Capture(cfg_capture, cfg_render)
    capture.run()


if __name__ == '__main__':
    main()
