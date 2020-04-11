import re
import logging
from typing import Union, TextIO, Sequence, Optional, Callable
from hpglope.render import RenderImageFormat, HpglRenderer, RenderException, RenderConfig


logger = logging.getLogger(__name__)


class HpglStreamParser:
    (
        ST_WAIT_CMD,        # Wait for command beginning
        ST_WAIT_SEMICOLON,  # Wait for normal termination with semicolon
        ST_WAIT_TERM,       # Wait for special LB, BL text terminator
        ST_RESYNC,          # Wait for semicolon and skip command buffer contents
    ) = range(4)

    def __init__(self, user_cmd_handler: Optional[Callable[[str], None]] = None):
        self.active = False
        self.buffer = ''
        # CMD extractor
        self.term = '\x03'  # Default text terminator: ETX character.
        self.state = self.ST_WAIT_CMD
        # Canvas
        self.canvas: Union[HpglRenderer, None] = None
        # File object to which we dump all the incoming HPGL
        self.hpgl_dump: Union[str, None] = None
        # User cmd handler
        self.user_cmd_handler = user_cmd_handler

    def resync(self):
        logger.warning('Parser panic: resyncing. Some commands may be skipped.')
        self.state = self.ST_RESYNC

    def start_plot(self, config: RenderConfig):
        if not self.active:
            logger.info('Starting plot.')
            self.active = True
            # Set up plotting / output
            self.canvas = HpglRenderer(config)
            self.hpgl_dump = ''

    def finish_plot(self, img_filename: str, img_format: RenderImageFormat, dump_filename: Optional[str] = None):
        if self.active:
            logger.info('Plot finished.')
            self.active = False
            if dump_filename:
                with open(dump_filename, 'wt') as f:
                    f.write(self.hpgl_dump)
            if img_filename:
                self.canvas.save(img_filename, img_format)
            self.canvas = None
            self.hpgl_dump = None

    def feed(self, b: bytes):
        # Bufferize new chunk of data
        self.buffer += b.decode('ascii')
        self.extract_cmd()

    def extract_cmd(self):
        while True:
            if self.state == self.ST_WAIT_CMD:
                # Waiting for cmd code
                if len(self.buffer) < 2:
                    break
                # Extract cmd code
                cmd = self.buffer[:2].upper()
                # Command should be two latin characters
                if not re.match(r'[A-Z][A-Z]', cmd):
                    logger.error('Invalid command: {!r}.'.format(cmd))
                    self.resync()
                    continue
                # Set next state depending on type of command
                if cmd in ('LB', 'BL'):
                    # Wait for special terminator
                    self.state = self.ST_WAIT_TERM
                else:
                    # Other commands: simply wait for semicolon terminator.
                    self.state = self.ST_WAIT_SEMICOLON
            elif self.state == self.ST_RESYNC:
                # Look for semicolon and skip buffer contents up to and including semicolon
                term_idx = self.buffer.find(';')
                if term_idx < 0:
                    self.buffer = ''
                    break
                self.buffer = self.buffer[(term_idx+1):]
                self.state = self.ST_WAIT_CMD
            elif self.state in (self.ST_WAIT_SEMICOLON, self.ST_WAIT_TERM):
                # Find first terminator in buffer and extract the complete command from buffer.
                term_idx = self.buffer.find(self.term if self.state == self.ST_WAIT_TERM else ';')
                if term_idx < 0:
                    break
                self.state = self.ST_WAIT_CMD
                # Handle cmd
                cmd = self.buffer[:(term_idx+1)]
                self.buffer = self.buffer[(term_idx+1):]
                # This can throw exception. If it does, we should not lose much.
                self.handle_command(cmd)
            else:
                raise RuntimeError('Invalid parser state: {!r}.'.format(self.state))

    def handle_command(self, cmd: str):
        logger.debug('cmd {!r}'.format(cmd))
        cmd_type = cmd[:2].upper()
        cmd_args = cmd[2:-1]
        # Call user's handler if set
        if self.user_cmd_handler:
            self.user_cmd_handler(cmd)
        # Write to dump
        if self.hpgl_dump is not None:
            self.hpgl_dump += cmd
        # Forward cmd to rendering engine
        if self.canvas is not None:
            try:
                self.canvas.process_command(cmd[:-1])
            except RenderException as e:
                logger.error('Drawing failed for command {!r}, reason: {!r}.'.format(cmd, e))
        # Some commands need special handling here
        if cmd_type == 'IN':
            # Reset parser settings
            self.term = '\x03'
        elif cmd_type == 'DT':
            # DT command - defines a new special terminator symbol
            if len(cmd_args) == 1:
                self.term = cmd_args[1]
            elif len(cmd_args) == 0:
                self.term = '\x03'
            else:
                logger.error('Bad {!r} command: {!r}'.format(cmd_type, cmd))

