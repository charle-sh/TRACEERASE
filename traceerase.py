#!/usr/bin/python3

import os, subprocess, argparse, hashlib, struct, random, platform, sys, curses, calendar
from datetime import datetime, timedelta
from time import sleep, mktime
from shutil import copyfile, which
from collections import namedtuple
try:
    #Text styling
    from colorama import Fore, Style
    success = Style.BRIGHT+Fore.GREEN+'[+]'+Style.RESET_ALL
    status = Style.BRIGHT+Fore.BLUE+'[*]'+Style.RESET_ALL
    bad = Style.BRIGHT+Fore.RED+'[-]'+Style.RESET_ALL
    fail = Style.BRIGHT+Fore.RED+'[!]'+Style.RESET_ALL
except ImportError:
    success = '[+]'
    status = '[*]'
    bad = '[-]'
    fail = '[!]'

#CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument('-a', '--auto', action='store_true', help=
    'Run the full script, automatically checking all logs that have changed since given timeframe'
    '. NOT YET FULLY IMPLEMENTED.')
parser.add_argument('-f', '--file', help=
    'Skip the automated log steps and clean specified log only.')
args = parser.parse_args()

#Constants
UTMP_STRUCT = struct.Struct('hi32s4s32s256shhiii4i20s') #utmp binary file struct
UTMP_FILES = ['/var/log/wtmp','/var/log/btmp','/var/share/adm/wtmpx','/var/share/adm/btmpx']
BLOCKSIZE = 65536

#Global variables
auto = args.auto
log_file = args.file

class Screen:
    def __init__(self, window, log):
        self._window = window
        self._log = log
        self._mode = 'select mode'
        self._message = ''
        self._row = 0
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
        self._hash = None
        self.fs = None
        self.fstype = None
        self.lines = []
        self.dirty_lines = []
        self._will_exit = False

        #Get line size
        if self._size % 382 == 0:
            self._line_size = 382
        elif self._size % 384 == 0:
            self._line_size = 384

        if self._size != 0:
            self._hash = get_hash(self.path, self._line_size)
            self._main()
        else:
            print(fail,self.path,'is empty')
            sleep(1)

    def _main(self):
        self._make_list()
        print(status+'Opening'+self.path+'...')
        sleep(1.5)
        curses.wrapper(Screen, self)
        if self._select() == 1:
            if self._size / self._line_size != len(self.dirty_lines):
                self._clean()
                self._get_mtime()
                touchback_am(self)
                get_fstype(self)
                touchback_c(self)
            else:
                wiper(self.path)
        elif self._select() == None:
            print(bad,self.path,'has not been changed because no dirty lines were selected!')


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
            for i, entry in enumerate(self._read(buf)):
                line = entry.user+'    '+entry.line+'    '+entry.host+'    '+str(entry.time)
                self.lines.append(line)

    def _select(self):
        if self.dirty_lines != []:
            self.clean_list = [l for i, l in enumerate(self.lines) if i not in sorted(self.dirty_lines)]
            bad_list = {i: l for i, l in enumerate(self.lines) if i in sorted(self.dirty_lines)}
            #Show lines to be removed
            sleep(1)
            print(status,'The following lines will be removed:\n')
            for key in bad_list:
                print(key, bad_list[key])
            while True:
                a = input('\n',status,'Do you want to continue? (y/n) ')
                if a == 'y':
                    return 1
                elif a == 'n':
                    print(status,'Reopening file...')
                    sleep(1)
                    break
                else:
                    print(fail,'Invalid option!')
            #Reopen file
            curses.wrapper(Screen, self)
            self._select()
        else:
            return

    def _clean(self):
        print(status,'Creating cleaned log file...')
        #List comprehension to remove user specified lines from log file
        self.clean_binary = [l for i, l in enumerate(self._binary) if i not in self.dirty_lines]
        #Check if log has new entries since script started
        if self._hash != get_hash(self.path, self._line_size):
            sleep(1)
            print(fail,self.path,'has changed since this script started!')
            sleep(0.5)
            print(status,'Automatically adding new entries to cleaned log...')
            with open(self.path, 'rb') as f:
                f.seek(self._size, 1)
                for line in f:
                    self.clean_binary.append(line)
        with open(self.path, 'wb') as f:
            for line in self.clean_binary:
                f.write(line)
        sleep(1)
        print(success,self.path,'is cleaned')
        sleep(1)

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

        if self._size != 0:
            self._hash = get_hash(self.path, BLOCKSIZE)
            self._main()
        else:
            print(fail,self.path,'is empty')
            sleep(1)

    def _main(self):
        self._make_list()
        print(status,'Opening',self.path,'...')
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
            print(bad,self.path,'has not been changed because no dirty lines were selected!')

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
            print(status,'The following lines will be removed:\n')
            for key in bad_list:
                print(key, bad_list[key])
            while True:
                a = input('\n',status,'Do you want to continue? (y/n) ')
                if a == 'y':
                    return 1
                elif a == 'n':
                    print(status,'Reopening file...')
                    sleep(1)
                    break
                else:
                    print(fail,'Invalid option!')
            #Reopen file
            curses.wrapper(Screen, self)
            self._select()
        else:
            return

    def _clean(self):
        print(status,'Creating cleaned log file...')
        #Check if log has new entries since script started
        if self._hash != get_hash(self.path, BLOCKSIZE):
            sleep(1)
            print(fail,self.path,'has changed since this script started!')
            sleep(0.5)
            print(status,'Automatically adding new entries to cleaned log...')
            with open(self.path, 'rb') as f:
                f.seek(self._size, 1)
                for line in f:
                    self.clean_list.append(str(line))
        with open(self.path, 'w') as f:
            f.write('\n'.join(self.clean_list)+'\n') #needs \n to not write next entry on same line
        sleep(1)
        print(success,self.path,'is cleaned')
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
                                                                   
    ''')

def get_os():
    print(status,'Getting OS information...')
    sleep(1)
    while True:
        answer = input(status,'System reports itself as',platform.system(),
            'is that correct? (y/n) ')
        if answer == 'y':
            system_os = platform.system()
            break
        elif answer == 'n':
            while True:
                answer2 = input('Is this system Linux (1) or Solaris (2)? (1/2) ')
                if answer2 == '1':
                    system_os = 'Linux'
                    break
                elif answer2 == '2':
                    system_os = 'Solaris'
                    break
                else:
                    print(fail,answer2,'is not a valid option!')
                    continue
            break
        else:
            print(fail,answer,'is not a valid option!')
            continue
    return system_os

def get_log_paths(system_os):
    if system_os == 'Linux':
        btmp_path = '/var/log/btmp'
        wtmp_path = '/var/log/wtmp'
        auth_log_path = '/var/log/auth.log'
        syslog_path = '/var/log/syslog'
        if os.path.isfile(btmp_path):
            btmp_path = '/var/log/btmp'
        else:
            print('that did not go well')
        if os.path.isfile(wtmp_path):
            wtmp_path = '/var/log/wtmp'
        else:
            print('that did not go well')
        if os.path.isfile(auth_log_path):
            auth_log_path = '/var/log/auth.log'
        else:
            print('that did not go well')
        if os.path.isfile(syslog_path):
            syslog_path = '/var/log/syslog'
        else:
            print('that did not go well')
    elif system_os == 'Solaris':
        btmp_path = '/var/adm/btmpx'
        wtmp_path = '/var/adm/wtmpx'
        if os.path.isfile(btmp_path):
            btmp_path = '/var/adm/btmpx'
        else:
            print('that did not go well')
        if os.path.isfile(wtmp_path):
            wtmp_path = '/var/adm/wtmpx'
        else:
            print('that did not go well')
    else:
        print(fail,system_os,'is unsupported!')
        exit()
    return btmp_path, wtmp_path, auth_log_path, syslog_path

def get_file_type(path):
    op = subprocess.Popen(['file', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = op.communicate()

    if 'ASCII' in str(stdout):
        return 'ASCII'
    elif 'data' in str(stdout):
        return 'data'
    elif 'empty' in str(stdout):
        return 'empty'
    else:
        print(fail,'Cannot clean this file!')
        return

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
        print(status,'/dev/null\'ing',log,'...')
        sleep(1)
        command = 'cat /dev/null > '+log
        dev_null = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(success,log,'is cleaned')

def touchback_am(log):
    #Gotta find latest remaining entry to the log to change the mtime back to that point
    print(status,'Timestomping atime and mtime...')
    sleep(1)
    os.utime(log.path, ns=(log.atime_ns, int(log.mtime_ns)))
    print(success,'Success!')

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
        print(bad,'Not sure where to look, this must not be Linux or Solaris! You monster...')

def touchback_c(log):
    #Check if the fstype is ext
    print(status,'Checking if ctime can be altered...')
    sleep(1)
    if 'ext' in log.fstype and which('debugfs'):
        print({success},'Log is stored on',log.fstype,'filesystem and debugfs is present, ctime can be changed!')
        get_ctime(log)
        while True:
            proceed = input('\nDo you want to use the native debugfs binary to edit the inode table? (y/n) ')
            if proceed == 'y':
                #Use debugfs to adjust ctime
                print('\n',status,'Stomping ctime...')
                sleep(1)
                command = "debugfs -w -R 'set_inode_field "+log.path+" ctime "+str(log.ctime)+"' "+log.fs
                op = subprocess.Popen(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                if log.fstype == 'ext4':
                    print(status,'Stomping ctime nanoseconds...')
                    sleep(1)
                    command2 = "debugfs -w -R 'set_inode_field "+log.path+" ctime_extra "+log.ctime_extra+"' "+log.fs
                    op2 = subprocess.Popen(command2, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                #Flush in-memory inode cache
                print(status,'Flushing in-memory inode cache...')
                sleep(1)
                update = "sync; echo 2 > /proc/sys/vm/drop_caches"
                op3 = subprocess.Popen(update, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                print(success,'All timestamps are stomped!')
                break
            elif proceed == 'n':
                print('\n',bad,'ctime remains unchanged and will not match mtime, should be fine...')
                break
            else:
                print('\n',fail,'Invalid option entered!')
                continue
    elif 'ext' in log.fstype and not which('debugfs'):
        print(bad,'Log is stored on',log.fstype,'filesystem, but debugfs is not present, '
            'ctime cannot be changed!')
    else:
        print(bad,'Cannot change ctime on',log.fstype,'filesystem!')

def get_ctime(log):
    timezone = mktime(datetime.now().timetuple()) - mktime(datetime.utcnow().timetuple())
    bad_ctime = int(str(log.mtime_ns)[:10]) - timezone
    log.ctime = datetime.fromtimestamp(bad_ctime).strftime('%Y%m%d%H%M%S')
    log.ctime_extra = str(int(str(log.mtime_ns)[-9:]) * 4)

def main():
    if auto:
        logo()
        #Find logs based on OS and user input
        system_os = get_os()
        btmp_path, wtmp_path, auth_log_path, syslog_path = get_log_paths(system_os)

        btmp = UtmpFile(btmp_path)
        wtmp = UtmpFile(wtmp_path)
        auth = AsciiFile(auth_log_path)
        syslog = AsciiFile(syslog_path)

    elif log_file != None:
        logo()
        if os.path.isfile(log_file) and get_file_type(log_file) == 'ASCII':
            text_log = AsciiFile(log_file)
        elif os.path.isfile(log_file) and get_file_type(log_file) == 'data' and log_file in UTMP_FILES:
            binary_log = UtmpFile(log_file)
        elif get_file_type(log_file) == 'empty':
            print(fail,log_file,'is empty! You\'re probably safe here...')
        else:
            print(fail,'This is not a supported log file! Exiting...')
    else:
        parser.print_help()
        
    exit()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n',status,'Exiting gracefully,whatever that means...')
        exit()
