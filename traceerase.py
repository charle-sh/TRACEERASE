#!/usr/bin/python3

try:
    #Text styling
    from colorama import Fore, Style
    success = Style.BRIGHT+Fore.GREEN+'[+] '+Style.RESET_ALL
    status = Style.BRIGHT+Fore.BLUE+'[*] '+Style.RESET_ALL
    bad = Style.BRIGHT+Fore.RED+'[-] '+Style.RESET_ALL
    fail = Style.BRIGHT+Fore.RED+'[!] '+Style.RESET_ALL
except ImportError:
    success = '[+] '
    status = '[*] '
    bad = '[-] '
    fail = '[!] '
try:
    import os, subprocess, argparse, hashlib, struct, random, platform, sys, curses, calendar
    from datetime import datetime, timedelta
    from time import sleep, mktime, strptime
    from shutil import copyfile, which
    from collections import namedtuple
    from getpass import getuser
    from pwd import getpwnam
except (ImportError) as e:
    print(fail+'[Error]: Import failure: '+str(e))
    exit()

#CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument('-d', '--discover', help=
    'Systematially check for all logs that have changed since specified timeframe (in minutes). '
    'Will search common log locations and open file descriptors based on process names.')
parser.add_argument('-p', '--process', nargs='+', help=
    'Check specified process\'s file descriptors for possible logging locations. Must be used in '
    'combination with time specified with -d.')
parser.add_argument('-f', '--file', help=
    'Skip the automated log steps and clean specified log only.')
args = parser.parse_args()

#Constants
UTMP_STRUCT = struct.Struct('hi32s4s32s256shhiii4i20s') #utmp struct
UTMPX_STRUCT = struct.Struct('32s4s32sih6xiii20x2x16s20x222x') #utmpx struct
LINUX_LASTLOG_STRUCT = struct.Struct('=l32s256s') #linux lastlog struct
LINUX_LASTLOG_STRUCT_WRITE = '=l32s256s'
SUN_LASTLOG_STRUCT = struct.Struct('=l8s16s') #sunos lastlog struct
SUN_LASTLOG_STRUCT_WRITE = '=l8s16s'
UTMP_FILES = ['/var/log/btmp','/var/log/wtmp','/var/run/utmp']
UTMPX_FILES = ['/var/share/adm/wtmpx','/var/share/adm/btmpx','/var/run/utmpx']
LINUX_LASTLOG_FILE = '/var/log/lastlog'
SUN_LASTLOG_FILE = '/var/share/adm/lastlog'
BLOCKSIZE = 65536
UTMPX_RECORD_SIZE = 372
LINUX_LASTLOG_RECORD_SIZE = 292
SUN_LASTLOG_RECORD_SIZE = 28

#Global variables
try:
    discover = args.discover
    if discover != None and int(discover) < 1: #make sure value is positive
        print(fail+'Value must be a positive integer!\n')
        parser.print_help()
        exit()
    process = args.process
    if process != None: #make sure value is positive
        for proc in process:
            if int(proc) < 1:
                print(fail+'Value must be a positive integer!\n')
                parser.print_help()
                exit()
except ValueError:
    print(fail+'Value must be a positive integer!\n')
    parser.print_help()
    exit()
log_file = args.file

class Screen:
    def __init__(self, window, log):
        self._window = window
        self._log = log
        self._mode = 'select mode'
        self._message = ''
        self._row = len(self._log.lines) - 1
        self._col = 0
        self._scroll_top = 0 # the first line number in the window
        self._will_exit = False

        self.main()

    def main(self):
        #Main loop
        while not self._will_exit:
            self.display()
            self._message = ''

            ch = self._window.getch()
            if self._mode == 'select mode':
                self._handle_select_keypress(ch)
            elif self._mode == 'Quit without saving? (y/n)':
                self._handle_nosave_keypress(ch)
            elif self._mode == 'Save selections? (y/n)':
                self._handle_save_keypress(ch)

            # TODO: get rid of this position clipping
            num_lines = len(self._log.lines)
            self._row = min(num_lines - 1, max(0, self._row))
            # on empty lines, still allow col 1
            num_cols = max(1, len(self._log.lines[self._row]))

    def _handle_select_keypress(self, ch):
        height, _ = self._window.getmaxyx()
        if ch in [ord('q'), 81]: #quit
            self._mode = 'Quit without saving? (y/n)'
        elif ch == curses.KEY_UP: #up
            self._row -= 1
        elif ch == curses.KEY_DOWN: #down
            self._row += 1
        elif ch == curses.KEY_PPAGE: #page up
            self._row -= height - 1
        elif ch == curses.KEY_NPAGE: #page down
            self._row += height - 1
        elif ch == curses.KEY_HOME: #home
            self._row = 0
        elif ch == curses.KEY_END: #end
            self._row = len(self._log.lines) - 1
        elif ch in [10]: #save selections and exit
            self._mode = 'Save selections? (y/n)'
        elif ch in [32]: #(un)select dirty line
            if self._row not in self._log.dirty_lines:
                self._message = 'Selected line '+ str(self._row + 1)
                self._log.dirty_lines.append(self._row) #select dirty line
            elif self._row in self._log.dirty_lines:
                self._message = 'Unselected line '+ str(self._row + 1)
                self._log.dirty_lines.remove(self._row) #unselect dirty line
        else:
            self._message = 'Unknown key: {}'.format(ch)        

    def _handle_nosave_keypress(self, ch):
        if ch in [ord('y'), 89]:
            self._log.dirty_lines.clear()
            self._will_exit = True
        elif ch in [ord('n'), 78]:
            self._mode = 'select mode'
        else:
            self._message = 'Unknown key: {}'.format(ch) 

    def _handle_save_keypress(self, ch):
        if ch in [ord('y'), 89]:
            self._will_exit = True
        elif ch in [ord('n'), 78]:
            self._mode = 'select mode'
        else:
            self._message = 'Unknown key: {}'.format(ch) 

    def display(self):
        #Display items in window
        self._window.erase()
        height, width = self._window.getmaxyx()
        self.draw_status_line(0, height - 1, width)
        self.draw_text(0, 0, width, height - 1)
        self._window.refresh()

    def draw_status_line(self, left, top, width):
        #Draw the status line
        mode = '{} {}'.format(self._mode.upper(), self._message).ljust(width - 1)
        self._window.addstr(top, left, mode, curses.A_REVERSE)
        #Print line position on left, number of selected lines on right
        position = '{} LN {}:{} '.format(self._log.path, self._row + 1, len(self._log.dirty_lines))
        self._window.addstr(top, left + width - 1 - len(position), position, curses.A_REVERSE)

    def draw_text(self, left, top, width, height):
        """Draw the text area."""
        # TODO: handle single lines that occupy the entire window
        highest_line_num = len(self._log.lines)
        gutter_width = max(3, len(str(highest_line_num))) + 1
        line_width = width - gutter_width # width to which text is wrapped
        cursor_y, cursor_x = None, None # where the cursor will be drawn

        # set scroll_top so the cursor is visible
        self._scroll_to(self._row, line_width, height)

        line_nums = range(self._scroll_top, highest_line_num)
        cur_y = top
        trailing_char = '~'

        for line_num in line_nums:

            # if there are no more rows left, break
            num_remaining_rows = top + height - cur_y
            if num_remaining_rows == 0:
                break

            # if all the wrapped lines can't fit on screen, break
            wrapped_lines = self._get_wrapped_lines(line_num, line_width)
            if len(wrapped_lines) > num_remaining_rows:
                trailing_char = '@'
                break

            # calculate cursor position if cursor must be on this line
            if line_num == self._row:
                lines = self._get_wrapped_lines(line_num, line_width,
                                                convert_nonprinting=False)
                real_col = len(self._convert_nonprinting(
                    ''.join(lines)[:self._col])
                )
                cursor_y = cur_y + real_col / line_width
                cursor_x = left + gutter_width + real_col % line_width

            # draw all the wrapped lines
            for n, wrapped_line in enumerate(wrapped_lines):
                if n == 0:
                    gutter = '{} '.format(line_num + 1).rjust(gutter_width)
                else:
                    gutter = ' ' * gutter_width
                self._window.addstr(cur_y, left, gutter, curses.A_REVERSE)
                if line_num in self._log.dirty_lines:
                    self._window.addstr(cur_y, left + len(gutter), wrapped_line, curses.A_STANDOUT)
                else:
                    self._window.addstr(cur_y, left + len(gutter), wrapped_line)
                cur_y += 1

        # draw empty lines
        for cur_y in range(cur_y, top + height):
            gutter = trailing_char.ljust(gutter_width)
            self._window.addstr(cur_y, left, gutter)

        # position the cursor
        assert cursor_x != None and cursor_y != None
        self._window.move(int(cursor_y) + 0, int(cursor_x) + 0)

    def _get_num_wrapped_lines(self, line_num, width):
        """Return the number of lines the given line number wraps to."""
        return len(self._get_wrapped_lines(line_num, width))

    def _get_wrapped_lines(self, line_num, width, convert_nonprinting=True):
        """Return the wrapped lines for the given line number."""
        def wrap_text(text, width):
            """Wrap string text into list of strings."""
            if text == '':
                yield ''
            else:
                for i in range(0, len(text), width):
                    yield text[i:i + width]
        assert line_num >= 0, 'line_num must be > 0'
        line = self._log.lines[line_num]
        if convert_nonprinting:
            line = self._convert_nonprinting(line)
        return list(wrap_text(line, width))

    def _scroll_bottom_to_top(self, bottom, width, height):
        """Return the first visible line's number so bottom line is visible."""
        def verify(top):
            """Verify the result of the parent function is correct."""
            rows = [list(self._get_wrapped_lines(n, width))
                    for n in range(top, bottom + 1)]
            num_rows = sum(len(r) for r in rows)
            assert top <= bottom, ('top line {} may not be below bottom {}'
                                   .format(top, bottom))
            assert num_rows <= height, (
                '{} rows between {} and {}, but only {} remaining. rows are {}'
                .format(num_rows, top, bottom, height, rows))

        top, next_top = bottom, bottom
        # distance in number of lines between top and bottom
        distance = self._get_num_wrapped_lines(bottom, width)

        # move top upwards as far as possible
        while next_top >= 0 and distance <= height:
            top = next_top
            next_top -= 1
            distance += self._get_num_wrapped_lines(max(0, next_top), width)

        verify(top)
        return top

    def _scroll_to(self, line_num, width, row_height):
        """Scroll so the line with the given number is visible."""
        # lowest scroll top that would still keep line_num visible
        lowest_top = self._scroll_bottom_to_top(line_num, width, row_height)

        if line_num < self._scroll_top:
            # scroll up until line_num is visible
            self._scroll_top = line_num
        elif self._scroll_top < lowest_top:
            # scroll down until line_num is visible
            self._scroll_top = lowest_top

    @staticmethod
    def _convert_nonprinting(text):
        """Replace nonprinting character in text."""
        # TODO: it would be nice if these could be highlighted when displayed
        res = []
        for char in text:
            i = ord(char)
            if char == '\t':
                res.append('->  ')
            elif i < 32 or i > 126:
                res.append('<{}>'.format(hex(i)[2:]))
            else:
                res.append(char)
        return ''.join(res)

class UtmpFile:
    #Just need the file path to start
    def __init__(self, path):
        self.path = path
        self._size = os.path.getsize(self.path)
        self.atime_ns = os.stat(self.path).st_atime_ns
        self.mtime_ns = None
        self.fs = None
        self.fstype = None
        self.lines = []
        self.dirty_lines = []
        self.cleaned_users = {}
        self.last_login = {}
        self.rolled_lines = []

        #Get line size
        if self._size % 382 == 0:
            self._line_size = 382
        elif self._size % 384 == 0:
            self._line_size = 384

        self._hash = get_hash(self.path, self._line_size)
        self._main()

    def _main(self):
        self._make_list()
        print(status+'Opening '+self.path+'...')
        sleep(1.5)
        curses.wrapper(Screen, self)
        if self._select() == 1:
            if self._size / self._line_size != len(self.dirty_lines):
                self._clean()
                self._get_mtime()
                if self.path == '/var/log/wtmp':
                    self.find_lastline()
                    last_log = LinuxLastLogFile(self)
                touchback_am(self)
                if self.path == '/var/log/wtmp':
                    touchback_am(last_log)
                get_fstype(self)
                touchback_c(self)
                if self.path == '/var/log/wtmp':
                    touchback_c(last_log)
            else:
                wiper(self.path)
                if self.path == '/var/log/wtmp':
                    self.find_lastline()
                    last_log = LinuxLastLogFile(self)
        elif self._select() == None:
            print(bad+self.path+' has not been changed because no dirty lines were selected!')
        sleep(1)
        print(success+'All actions on '+self.path+' completed.')
        sleep(1.5)

    def _make_list(self):
        #Get binary
        self._binary = []
        with open(self.path, 'rb') as f:
            buf = f.read(self._line_size)
            while buf:
                self._binary.append(buf)
                buf = f.read(self._line_size)
        #Get strings version for pager
        with open(self.path, 'rb') as f:
            buf = f.read()
            for entry in self._read(buf):
                line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                self.lines.append(line)

    def _select(self):
        if self.dirty_lines != []:
            self.clean_list = [l for i, l in enumerate(self.lines) if i not in sorted(self.dirty_lines)]
            bad_list = {i: l for i, l in enumerate(self.lines) if i in sorted(self.dirty_lines)}
            #Show lines to be removed
            sleep(1)
            print(status+'The following lines will be removed:\n')
            for key in bad_list:
                print(key, bad_list[key])
                ll_list = bad_list[key].split()
                try:
                    self.cleaned_users[getpwnam(ll_list[0]).pw_uid] = ll_list[0]
                except KeyError:
                    pass
            while True:
                a = input('\n'+status+'Do you want to continue? (y/n) ')
                if a == 'y':
                    return 1
                elif a == 'n':
                    print(status+'Reopening file...')
                    self.cleaned_users.clear()
                    sleep(1)
                    break
                else:
                    print(fail+'Invalid option!')
            #Reopen file
            curses.wrapper(Screen, self)
            self._select()
        else:
            return

    def _clean(self):
        print(status+'Creating cleaned log file...')
        #List comprehension to remove user specified lines from log file
        self.clean_binary = [l for i, l in enumerate(self._binary) if i not in self.dirty_lines]
        #Check if log has new entries since script started
        if self._hash != get_hash(self.path, self._line_size):
            sleep(1)
            print(fail+self.path+' has changed since this script started!')
            sleep(0.5)
            print(status+'Automatically adding new entries to cleaned log...')
            with open(self.path, 'rb') as f:
                f.seek(self._size)
                for line in f:
                    self.clean_binary.append(line)
                f.seek(self._size)
                for entry in self._read(f.read()):
                    line_ = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                    self.clean_list.append(line_)
        with open(self.path, 'wb') as f:
            for line in self.clean_binary:
                f.write(line)
        sleep(0.5)
        print(success+self.path+' is cleaned')

    def find_lastline(self):
        for uid in self.cleaned_users:
            if self.clean_list != []:
                for line in reversed(self.clean_list):
                    if self.cleaned_users[uid] in line.split()[0]:
                        self.last_login[uid] = line
                        break
                    else:
                        #Read through rolled logs
                        next_log = 1
                        while len(self.last_login) < len(self.cleaned_users):
                            rolled_path = self.path + '.' + str(next_log)
                            if os.path.isfile(rolled_path):
                                with open(rolled_path, 'rb') as f:
                                    buf = f.read()
                                    for entry in self._read(buf):
                                        line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                                        self.rolled_lines.append(line)
                                for line in reversed(self.rolled_lines):
                                    if self.cleaned_users[uid] in line.split()[0]:
                                        self.last_login[uid] = line
                                        break
                                next_log += 1
                            else:
                                self.last_login[uid] = b'\x00' * LINUX_LASTLOG_RECORD_SIZE
                                break
            else:
                #Read through rolled logs
                next_log = 1
                while len(self.last_login) < len(self.cleaned_users):
                    rolled_path = self.path + '.' + str(next_log)
                    if os.path.isfile(rolled_path):
                        with open(rolled_path, 'rb') as f:
                            buf = f.read()
                            for entry in self._read(buf):
                                line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                                self.rolled_lines.append(line)
                        for line in reversed(self.rolled_lines):
                            if self.cleaned_users[uid] in line.split()[0]:
                                self.last_login[uid] = line
                                break
                        next_log += 1
                    else:
                        self.last_login[uid] = b'\x00' * LINUX_LASTLOG_RECORD_SIZE
                        break

    def _get_mtime(self):
        offset = 0
        with open(self.path, 'rb') as f:
            f.seek((self._line_size * -1), 2)
            buf = f.read()
            last_line = UtmpRecord._make(map(self._convert_string, UTMP_STRUCT.unpack_from(buf, offset)))
        self.mtime_ns = last_line.mtime_ns
        while len(self.mtime_ns) < 19:
            rand = random.randint(0,9)
            self.mtime_ns += str(rand)

    def _read(self, buf):
        offset = 0
        while offset < len(buf):
            yield UtmpRecord._make(map(self._convert_string, UTMP_STRUCT.unpack_from(buf, offset)))
            offset += UTMP_STRUCT.size

    def _convert_string(self, val):
        if isinstance(val, bytes):
            return val.rstrip(b'\0').decode()
        return val

class UtmpRecord(namedtuple('utmprecord','type pid line id user host exit0 exit1 session sec usec addr0 addr1 addr2 addr3 unused')):
    #Convert epoch time to normal datetime
    @property
    def time(self):
        return datetime.fromtimestamp(self.sec) + timedelta(microseconds=self.usec)

    @property
    def mtime_ns(self):
        return str(self.sec) + str(self.usec)

class LinuxLastLogFile:
    def __init__(self, log):
        self._log = log
        self.path = LINUX_LASTLOG_FILE
        self._size = os.path.getsize(self.path)
        self.atime_ns = os.stat(self.path).st_atime_ns
        self.mtime_ns = None
        self.fs = None
        self.fstype = None
        self._main()

    def _main(self):
        print(status+'Automatically matching '+self.path+' to '+self._log.path+'...')
        self._clean()
        print(success+self.path+' is cleaned!')
        get_fstype(self)
        self._get_mtime()

    def _clean(self):
        with open(self.path, 'rb+') as f:
            for uid in self._log.last_login:
                if self._log.last_login[uid] != b'\x00' * LINUX_LASTLOG_RECORD_SIZE:
                    if len(self._log.last_login[uid].split()) == 5:
                        _, last_term, last_host, last_date, last_time = self._log.last_login[uid].split()
                    elif len(self._log.last_login[uid].split()) == 4:
                        _, last_term, last_date, last_time = self._log.last_login[uid].split()
                        last_host = '\x00'
                    if '.' in last_time:
                        last_time, _ = last_time.split('.')
                    last_datetime = last_date+' '+last_time
                    pattern = '%Y-%m-%d %H:%M:%S'
                    epoch = int(mktime(strptime(last_datetime, pattern)))
                    f.seek(uid * LINUX_LASTLOG_RECORD_SIZE)
                    f.write(struct.pack(LINUX_LASTLOG_STRUCT_WRITE, epoch, bytes(last_term, 'ascii'), bytes(last_host, 'ascii')))
                else:
                    f.seek(uid * LINUX_LASTLOG_RECORD_SIZE)
                    f.write(self._log.last_login[uid])

    def _get_mtime(self):
        logins = []
        with open(LINUX_LASTLOG_FILE, 'rb') as f:
            buf = f.read()
        offset = 0
        while offset < len(buf):
            epoch, _, _ = LINUX_LASTLOG_STRUCT.unpack_from(buf, offset)
            if epoch != 0:
                logins.append(epoch)
            offset += LINUX_LASTLOG_STRUCT.size
        self.mtime_ns = str(max(logins))
        while len(self.mtime_ns) < 19:
            rand = random.randint(0,9)
            self.mtime_ns += str(rand)

class UtmpxFile:
    def __init__(self, path):
        self.path = path
        self._size = os.path.getsize(self.path)
        self.atime_ns = os.stat(self.path).st_atime_ns
        self.mtime_ns = None
        self.fs = None
        self.fstype = None
        self.lines = []
        self.dirty_lines = []
        self.cleaned_users = {}
        self.last_login = {}
        self.rolled_lines = []

        self._hash = get_hash(self.path, UTMPX_RECORD_SIZE)
        self._main()

    def _main(self):
        self._make_list()
        print(status+'Opening '+self.path+'...')
        sleep(1.5)
        curses.wrapper(Screen, self)
        if self._select() == 1:
            if self._size / UTMPX_RECORD_SIZE != len(self.dirty_lines):
                self._clean()
                self._get_mtime()
                if self.path == '/var/share/adm/wtmpx':
                    self.find_lastline()
                    last_log = SunLastLogFile(self)
                touchback_am(self)
                if self.path == '/var/share/adm/wtmpx':
                    touchback_am(last_log)
                get_fstype(self)
                touchback_c(self)
                if self.path == '/var/share/adm/wtmpx':
                    touchback_c(last_log)
            else:
                wiper(self.path)
                if self.path == '/var/share/adm/wtmpx':
                    self.find_lastline()
                    last_log = LinuxLastLogFile(self)
        elif self._select() == None:
            print(bad+self.path+' has not been changed because no dirty lines were selected!')
        sleep(1)
        print(success+'All actions on '+self.path+' completed.')
        sleep(1.5)

    def _make_list(self):
        #Get binary
        self._binary = []
        with open(self.path, 'rb') as f:
            buf = f.read(UTMPX_RECORD_SIZE)
            while buf:
                self._binary.append(buf)
                buf = f.read(UTMPX_RECORD_SIZE)
        #Get strings version for pager
        with open(self.path, 'rb') as f:
            buf = f.read()
            for i, entry in enumerate(self._read(buf)):
                line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)+'    '+entry.type
                self.lines.append(line)

    def _select(self):
        if self.dirty_lines != []:
            self.clean_list = [l for i, l in enumerate(self.lines) if i not in sorted(self.dirty_lines)]
            bad_list = {i: l for i, l in enumerate(self.lines) if i in sorted(self.dirty_lines)}
            #Show lines to be removed
            sleep(1)
            print(status+'The following lines will be removed:\n')
            for key in bad_list:
                print(key, bad_list[key])
                ll_list = bad_list[key].split()
                try:
                    self.cleaned_users[getpwnam(ll_list[0]).pw_uid] = ll_list[0]
                except KeyError:
                    pass
            while True:
                a = input('\n'+status+'Do you want to continue? (y/n) ')
                if a == 'y':
                    return 1
                elif a == 'n':
                    print(status+'Reopening file...')
                    self.cleaned_users.clear()
                    sleep(1)
                    break
                else:
                    print(fail+'Invalid option!')
            #Reopen file
            curses.wrapper(Screen, self)
            self._select()
        else:
            return

    def _clean(self):
        print(status+'Creating cleaned log file...')
        #List comprehension to remove user specified lines from log file
        self.clean_binary = [l for i, l in enumerate(self._binary) if i not in self.dirty_lines]
        #Check if log has new entries since script started
        if self._hash != get_hash(self.path, UTMPX_RECORD_SIZE):
            sleep(1)
            print(fail+self.path+' has changed since this script started!')
            sleep(0.5)
            print(status+'Automatically adding new entries to cleaned log...')
            with open(self.path, 'rb') as f:
                f.seek(self._size)
                for line in f:
                    self.clean_binary.append(line)
                f.seek(self._size)
                for entry in self._read(f.read()):
                    line_ = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)+'    '+entry.type
                    self.clean_list.append(line_)
        with open(self.path, 'wb') as f:
            for line in self.clean_binary:
                f.write(line)
        sleep(0.5)
        print(success+self.path+' is cleaned!')

    def find_lastline(self):
        for uid in self.cleaned_users:
            if self.clean_list != []:
                for line in reversed(self.clean_list):
                    if self.cleaned_users[uid] in line.split()[0] and 'USER_PROCESS' in line:
                        self.last_login[uid] = line
                        break
                    else:
                        #Read through rolled logs
                        next_log = 0
                        while len(self.last_login) < len(self.cleaned_users):
                            rolled_path = self.path + '.' + str(next_log)
                            if os.path.isfile(rolled_path):
                                with open(rolled_path, 'rb') as f:
                                    buf = f.read()
                                    for entry in self._read(buf):
                                        line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                                        self.rolled_lines.append(line)
                                for line in reversed(self.rolled_lines):
                                    if self.cleaned_users[uid] in line.split()[0] and 'USER_PROCESS' in line:
                                        self.last_login[uid] = line
                                        break
                                next_log += 1
                            else:
                                self.last_login[uid] = b'\x00' * SUN_LASTLOG_RECORD_SIZE
                                break
            else:
                #Read through rolled logs
                next_log = 0
                while len(self.last_login) < len(self.cleaned_users):
                    rolled_path = self.path + '.' + str(next_log)
                    if os.path.isfile(rolled_path):
                        with open(rolled_path, 'rb') as f:
                            buf = f.read()
                            for entry in self._read(buf):
                                line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                                self.rolled_lines.append(line)
                        for line in reversed(self.rolled_lines):
                            if self.cleaned_users[uid] in line.split()[0] and 'USER_PROCESS' in line:
                                self.last_login[uid] = line
                                break
                        next_log += 1
                    else:
                        self.last_login[uid] = b'\x00' * SUN_LASTLOG_RECORD_SIZE
                        break

    def _get_mtime(self):
        offset = 0
        with open(self.path, 'rb') as f:
            f.seek((UTMPX_RECORD_SIZE * -1), 2)
            buf = f.read()
            last_line = UtmpxRecord._make(map(self._convert_string, UTMPX_STRUCT.unpack_from(buf, offset)))
        self.mtime_ns = last_line.mtime_ns
        while len(self.mtime_ns) < 19:
            rand = random.randint(0,9)
            self.mtime_ns += str(rand)

    def _read(self, buf):
        offset = 0
        while offset < len(buf):
            yield UtmpxRecord._make(map(self._convert_string, UTMPX_STRUCT.unpack_from(buf, offset)))
            offset += UTMPX_STRUCT.size

    def _convert_string(self, val):
        if isinstance(val, bytes):
            return val.rstrip(b'\0').decode()
        return val

class UtmpxRecord(namedtuple('utmpxrecord','user id line pid ut_type sec usec session host')):
    #Convert epoch time to normal datetime
    @property
    def time(self):
        return datetime.fromtimestamp(self.sec) + timedelta(microseconds=self.usec)

    @property
    def type(self):
        return {
          0: 'EMPTY',
          1: 'RUN_LVL',
          2: 'BOOT_TIME',
          3: 'NEW_TIME',
          4: 'OLD_TIME',
          5: 'INIT_PROCESS',
          6: 'LOGIN_PROCESS',
          7: 'USER_PROCESS',
          8: 'DEAD_PROCESS',
          9: 'ACCOUNTING'
        }.get(self.ut_type, 'UNKNOWN')
    
    @property
    def mtime_ns(self):
        return str(self.sec) + str(self.usec)

class SunLastLogFile:
    def __init__(self, log):
        self._log = log
        self.path = SUN_LASTLOG_FILE
        self._size = os.path.getsize(self.path)
        self.atime_ns = os.stat(self.path).st_atime_ns
        self.mtime_ns = None
        self.fs = None
        self.fstype = None
        self._main()

    def _main(self):
        print(status+'Automatically matching '+self.path+' to '+self._log.path+'...')
        self._clean()
        print(success+self.path+' is cleaned!')
        get_fstype(self)
        self._get_mtime()

    def _clean(self):
        with open(self.path, 'rb+') as f:
            for uid in self._log.last_login:
                if self._log.last_login[uid] != b'\x00' * SUN_LASTLOG_RECORD_SIZE:
                    if len(self._log.last_login[uid].split()) == 6:
                        _, _, last_host, last_date, last_time, _ = self._log.last_login[uid].split()
                        last_term = 'ssh'
                    elif len(self._log.last_login[uid].split()) == 5:
                        _, last_term, last_date, last_time, _ = self._log.last_login[uid].split()
                        last_host = '\x00'
                    if '.' in last_time: #remove ns time from last_time
                        last_time, last_time_ns = last_time.split('.')
                    last_datetime = last_date+' '+last_time
                    pattern = '%Y-%m-%d %H:%M:%S'
                    epoch = int(mktime(strptime(last_datetime, pattern)))
                    f.seek(uid * SUN_LASTLOG_RECORD_SIZE)
                    f.write(struct.pack(SUN_LASTLOG_STRUCT_WRITE, epoch, bytes(last_term, 'ascii'), bytes(last_host, 'ascii')))
                else:
                    f.seek(uid * SUN_LASTLOG_RECORD_SIZE)
                    f.write(self._log.last_login[uid])

    def _get_mtime(self):
        logins = []
        with open(SUN_LASTLOG_FILE, 'rb') as f:
            buf = f.read()
        offset = 0
        while offset < len(buf):
            epoch, _, _ = SUN_LASTLOG_STRUCT.unpack_from(buf, offset)
            if epoch != 0:
                logins.append(epoch)
            offset += SUN_LASTLOG_STRUCT.size
        self.mtime_ns = str(max(logins))
        while len(self.mtime_ns) < 19:
            rand = random.randint(0,9)
            self.mtime_ns += str(rand)

class AsciiFile:
    #Just need the file path to start
    def __init__(self, path):
        self.path = path
        self._size = os.path.getsize(self.path)
        self.atime_ns = os.stat(self.path).st_atime_ns
        self.mtime_ns = None
        self._hash = None
        self.fs = None
        self.fstype = None
        self.lines = []
        self.dirty_lines = []
        self._hash = get_hash(self.path, BLOCKSIZE)
        self._main()

    def _main(self):
        self._make_list()
        print(status+'Opening '+self.path+'...')
        sleep(1.5)
        curses.wrapper(Screen, self)

        if self._select() == 1:
            if len(self.lines) != len(self.dirty_lines):
                self._clean()
                self._get_mtime()
                touchback_am(self)
                get_fstype(self)
                touchback_c(self)
            else:
                wiper(self.path)
        elif self._select() == None:
            print(bad+self.path+' has not been changed because no dirty lines were selected!')
        sleep(1)
        print(success+'All actions on '+self.path+' completed.')
        sleep(1.5)

    def _make_list(self):
        text = ''
        with open(self.path, 'r') as f:
            text = f.read()
        self.lines = text.split('\n')[:-1] #last line is always blank for some reason

    def _select(self):
        if self.dirty_lines != []:
            self.clean_list = [l for i, l in enumerate(self.lines) if i not in sorted(self.dirty_lines)]
            bad_list = {i: l for i, l in enumerate(self.lines) if i in sorted(self.dirty_lines)}
            #Show lines to be removed
            sleep(1)
            print(status+'The following lines will be removed:\n')
            for key in bad_list:
                print(key, bad_list[key])
            while True:
                a = input('\n'+status+'Do you want to continue? (y/n) ')
                if a == 'y':
                    return 1
                elif a == 'n':
                    print(status+'Reopening file...')
                    sleep(1)
                    break
                else:
                    print(fail+'Invalid option!')
            #Reopen file
            curses.wrapper(Screen, self)
            self._select()
        else:
            return

    def _clean(self):
        print(status+'Creating cleaned log file...')
        #Check if log has new entries since script started
        if self._hash != get_hash(self.path, BLOCKSIZE):
            sleep(1)
            print(fail+self.path+' has changed since this script started!')
            sleep(0.5)
            print(status+'Automatically adding new entries to cleaned log...')
            with open(self.path, 'rb') as f:
                f.seek(self._size, 1)
                for line in f:
                    self.clean_list.append(str(line))
        with open(self.path, 'w') as f:
            f.write('\n'.join(self.clean_list)+'\n') #needs \n to not write next entry on same line
        sleep(1)
        print(success+self.path+' is cleaned')
        sleep(1)

    def _get_mtime(self):
        month, day, time, _ = self.clean_list[-1].split(' ', 3)
        hour, minute, second = time.split(':')
        year = datetime.today().year
        abbr_to_num = {name: num for num, name in enumerate(calendar.month_abbr) if num}
        month_int = abbr_to_num[month]
        mtime = datetime(year,month_int,int(day),int(hour),int(minute)).timestamp()
        mtime_s = int(mtime) + int(second)
        ns = ''
        while len(ns) < 9:
            rand = random.randint(0,9)
            ns += str(rand)
        self.mtime_ns = str(mtime_s) + str(ns)

def logo():
    print('''
  _____ ____      _    ____ _____ _____ ____      _    ____  _____ 
 |_   _|  _ \    / \  / ___| ____| ____|  _ \    / \  / ___|| ____|
   | | | |_) |  / _ \| |   |  _| |  _| | |_) |  / _ \ \___ \|  _|  
   | | |  _ <  / ___ \ |___| |___| |___|  _ <  / ___ \ ___) | |___ 
   |_| |_| \_\/_/   \_\____|_____|_____|_| \_\/_/   \_\____/|_____|
                              0.1.0.0                              

    ''')

def get_os():
    print(status+'Getting OS information...')
    sleep(1)
    while True:
        answer = input(status+'System reports itself as '+platform.system()+
            ' is that correct? (y/n) ')
        if answer == 'y':
            system_os = platform.system()
            break
        elif answer == 'n':
            while True:
                answer2 = input('Is this system Linux (1) or SunOS (2)? (1/2) ')
                if answer2 == '1':
                    system_os = 'Linux'
                    break
                elif answer2 == '2':
                    system_os = 'SunOS'
                    break
                else:
                    print(fail+answer2+' is not a valid option!')
                    continue
            break
        else:
            print(fail+answer+' is not a valid option!')
            continue
    return system_os

def logcheck(*process):
    not_logs = ['/proc/sys/kernel/hostname','/proc/kmsg']
    log_processes = ['syslogd','rsyslogd','systemd-journal','auditd']
    if process == ():
        pids = [pid for pid in os.listdir('/proc') if pid.isdigit()]
    else:
        pids = process
    varlog = []
    varshareadm = []
    log_files = {}

    '''
    I am strongly considering spliting these up and doing the platform.system() conditional to 
    call different functions.
    '''
    if platform.system() == 'Linux':
        #Check processes on Linux
        for pid in pids:
            comm = '/proc/'+pid+'/comm'
            fd = '/proc/'+pid+'/fd/'
            try:
                with open(comm, 'r') as f:
                    process = f.read().rstrip()
                    if process in log_processes:
                        proc_dir = os.listdir(fd)
                        for link in proc_dir:
                            file = os.readlink(fd+link)
                            if os.path.isfile(file) and file not in not_logs and os.path.getsize(file) != 0:
                                log_files[file] = process
            except IOError: # proc has already terminated
                continue
    elif platform.system() == 'SunOS':
        #Check processes on Solaris
        varlog_list = ['/var/log/'+file for file in os.listdir('/var/log/')]
        varadm_list = ['/var/adm/'+file for file in os.listdir('/var/adm/')]
        varshareadm_list = ['/var/share/adm/'+file for file in os.listdir('/var/share/adm/')]
        varshareaudit_list = ['/var/share/audit/'+file for file in os.listdir('/var/share/audit/')]
        all_logs_list = varlog_list + varadm_list + varshareadm_list + varshareaudit_list

        for pid in pids:
            comm = '/proc/'+pid+'/execname'
            fd = '/proc/'+pid+'/fd/'
            try:
                with open(comm, 'r') as f:
                    process = f.read().split('/')[-1].rstrip('\x00')
                    if process in log_processes:
                        proc_dir = os.listdir(fd)
                        for num in proc_dir:
                            file_desc = fd+num
                            for n, file in enumerate(all_logs_list):
                                if os.stat(file_desc).st_ino == os.stat(file).st_ino:
                                    log_files[file] = process
                                    break
                                elif n == len(all_logs_list) - 1 and get_file_type(file_desc) != 'not_log':
                                    log_files[file_desc] = process
            except IOError as e: # proc has already terminated
                print(e)
                continue

    if process == ():
        logcheck_filesys(log_files)
    else:
        get_changed_logs(log_files)

def logcheck_filesys(log_files):
    #Check dirs
    try:
        varlog = ['/var/log/'+file for file in os.listdir('/var/log') if '/var/log/'+file not in log_files]
        varlog.append('/var/run/utmp')
        varshareadm = ['/var/share/adm/'+file for file in os.listdir('/var/share/adm/') if '/var/share/adm/'+file not in log_files]
        varshareadm.append('/var/run/utmpx')
    except FileNotFoundError: #For locations that only exist on Solaris
        pass
    all_logs = varlog + varshareadm
    for log in all_logs:
        if os.path.isfile(log) and get_file_type(log) == 'data' and log in UTMP_FILES:
            log_files[log] = 'utmp'
        elif os.path.isfile(log) and get_file_type(log) == 'data' and log in UTMPX_FILES:
            log_files[log] = 'utmpx'
        elif os.path.isfile(log) and get_file_type(log) == 'ASCII':
            log_files[log] = 'ascii'

    get_changed_logs(log_files)

def get_changed_logs(log_files):
    changed_logs = 0 #to keep track if any logs changed
    cleanable_logs = ['syslogd','rsyslogd','utmp','utmpx','ascii'] #log types we can clean
    #Check if logs changed
    for log in log_files:
        mtime = os.path.getmtime(log)
        last_modified_date = datetime.fromtimestamp(mtime)
        difference = datetime.now() - last_modified_date
        if timedelta(minutes=0) <= difference <= timedelta(minutes=int(discover)):
            if log_files[log] in cleanable_logs and log.split('/')[1] != 'proc':
                print(bad+log+' has changed in last '+discover+' minutes')
                changed_logs += 1
            elif log_files[log] in cleanable_logs and log.split('/')[1] == 'proc':
                print(bad+log+' is a '+log_files[log]+' '+get_file_type(log)+' file and has changed in last '+discover+' minutes')
                changed_logs += 1
            elif log_files[log] not in cleanable_logs and log.split('/')[1] == 'proc':
                print(fail+log+' is a '+log_files[log]+' '+get_file_type(log)+' file and has changed in last '+discover+' minutes but can\'t be cleaned!')
                changed_logs += 1
            else:
                print(fail+log+' has changed in last '+discover+' minutes but can\'t be cleaned!')
                changed_logs += 1

    #Say if logs haven't changed
    if changed_logs == 0:
        print(success+'No logs have changed in the last '+discover+' minutes.')

'''
Couldn't figure out a way to not use the 'file' binary to find file types, might revist in the 
future...
'''
def get_file_type(path):
    op = subprocess.Popen(['file', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = op.communicate()
    stdout = stdout.decode().split()

    if 'ASCII' in stdout:
        return 'ASCII'
    elif 'ascii' in stdout:
        return 'ASCII'
    elif 'data' in stdout:
        return 'data'
    elif 'dBase' in stdout:
        return 'data'
    elif 'empty' in stdout:
        return 'empty'
    elif 'Solaris' in stdout and 'Audit' in stdout:
        return 'Solaris Audit'
    else:
        return 'not_log'

def get_hash(path, block_size):
    #Get file hash
    hasher = hashlib.md5()
    with open(path, 'rb') as f:
        buf = f.read(block_size)
        while buf:
            hasher.update(buf)
            buf = f.read(block_size)
    return hasher.hexdigest()

def wiper(log):
    #cat /dev/null into file if you're wiping all lines
    print(status+'/dev/null\'ing '+log+'...')
    sleep(1)
    command = 'cat /dev/null > '+log
    dev_null = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(success+log+' is cleaned')

def touchback_am(log):
    #Gotta find latest remaining entry to the log to change the mtime back to that point
    print(status+'Timestomping '+log.path+' atime and mtime...')
    sleep(1)
    os.utime(log.path, ns=(log.atime_ns, int(log.mtime_ns)))
    print(success+'Success!')

def get_fstype(log):
    #Determine mount point for log file
    path = os.path.abspath(log.path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    #Linux branch
    if os.path.isfile('/proc/mounts'):
        with open('/proc/mounts', 'r') as f:
            for line in f.readlines():
                fs, mount_point, fstype, _, _, _ = line.split()
                if mount_point == path:
                    log.fs = fs
                    log.fstype = fstype
    #Solaris branch
    elif os.path.isfile('/etc/mnttab'):
        with open('/etc/mnttab', 'r') as f:
            for line in f.readlines():
                fs, mount_point, fstype, _, _ = line.split()
                if mount_point == path:
                    log.fs = fs
                    log.fstype = fstype
    #WTF is this branch
    else:
        print(bad+'Not sure where to look, this must not be Linux or Solaris! You monster...')

def touchback_c(log):
    #Check if the fstype is ext
    print(status+'Checking if ctime can be altered...')
    sleep(1)
    if 'ext' in log.fstype and which('debugfs'):
        print(success+'Log is stored on '+log.fstype+' filesystem and debugfs is present, ctime can be changed!')
        get_ctime(log)
        while True:
            proceed = input('\nDo you want to use the native debugfs binary to edit the inode table for '+log.path+'? (y/n) ')
            if proceed == 'y':
                #Use debugfs to adjust ctime
                print('\n'+status+'Stomping ctime...')
                sleep(1)
                command = "debugfs -w -R 'set_inode_field "+log.path+" ctime "+str(log.ctime)+"' "+log.fs
                op = subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                if log.fstype == 'ext4':
                    print(status+'Stomping ctime nanoseconds...')
                    sleep(1)
                    command2 = "debugfs -w -R 'set_inode_field "+log.path+" ctime_extra "+log.ctime_extra+"' "+log.fs
                    op2 = subprocess.Popen(command2, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                #Flush in-memory inode cache
                print(status+'Flushing in-memory inode cache...')
                sleep(1)
                update = "sync; echo 2 > /proc/sys/vm/drop_caches"
                op3 = subprocess.Popen(update, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                print(success+'All timestamps are stomped!')
                break
            elif proceed == 'n':
                print('\n'+bad+'ctime remains unchanged and will not match mtime, should be fine...')
                break
            else:
                print('\n'+fail+'Invalid option entered!')
                continue
    elif 'ext' in log.fstype and not which('debugfs'):
        print(bad+'Log is stored on '+log.fstype+' filesystem, but debugfs is not present, '
            'ctime cannot be changed!')
    else:
        print(bad+'Cannot change ctime on '+log.fstype+' filesystem!')

def get_ctime(log):
    timezone = mktime(datetime.now().timetuple()) - mktime(datetime.utcnow().timetuple())
    bad_ctime = int(str(log.mtime_ns)[:10]) - timezone
    log.ctime = datetime.fromtimestamp(bad_ctime).strftime('%Y%m%d%H%M%S')
    log.ctime_extra = str(int(str(log.mtime_ns)[-9:]) * 4)

def main():
    if getuser() != 'root':
        print(fail+'Script must be run as root!')
        exit()

    elif discover != None and process == None:
        logcheck()
        exit()

    elif discover != None and process != None:
        logcheck(*process)
        exit()

    elif discover == None and process != None:
        parser.print_help()
        exit()

    elif log_file != None:
        if os.path.isfile(log_file) == False:
            print(fail+'File does not exist!')
            exit()
        elif os.path.getsize(log_file) == 0 or get_file_type(log_file) == 'empty':
            print(success+log_file+' is empty! You\'re probably safe here...')
        elif os.path.isfile(log_file) and get_file_type(log_file) == 'ASCII':
            logo()
            text_log = AsciiFile(log_file)
        elif os.path.isfile(log_file) and get_file_type(log_file) == 'data' and log_file in UTMP_FILES:
            logo()
            binary_log = UtmpFile(log_file)
        elif os.path.isfile(log_file) and get_file_type(log_file) == 'data' and log_file in UTMPX_FILES:
            logo()
            binary_log = UtmpxFile(log_file)
        elif os.path.isfile(log_file) and get_file_type(log_file) == 'data' and log_file == LINUX_LASTLOG_FILE:
            logo()
            binary_log = LinuxLastLogFile(log_file)        
        else:
            print(fail+'This is not a supported log file! Exiting...')
    else:
        parser.print_help()
    sleep(0.5)
    print(status+'Closing TRACEERASE...') 
    exit()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt: #handle keyboard interrupts
        print('\n'+bad+'Interrupted! Closing TRACEERASE...')
        exit()
