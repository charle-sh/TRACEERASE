# TRACEERASE
TRACEERASE is a tool designed to clean various Unix logs. As of now, it can clean ASCII logs that adhere to the syslog RFC3164 format and utmp-formatted binary logs. Other log types may be implemented in the future. TRACEERASE features a custom built-in log viewer for selecting individual dirty lines, smart timestamp manipulation (atime, mtime, sometimes ctime), automatically adding newly-generated log entries back into the log even after opening it, and memory-only execution. It also relies very little on system binaries, instead using built-in functionality, to avoid dependending on tools like `utmpdump` or `grep`. The tool is built entirely on the Python 3 standard library to avoid having to import 3rd party modules on the system. If the system has Python 3 installed, it can run this script.

## Background
I originally started writing this as a way to get into more advanced Python scripting and to learn more about Unix logging. I noticed that most other scripts on GitHub and elsewhere either cleaned any instance of an IP address or username from a log, or wiped the log entirely. The latter is obviously easy to implement, but also easy to catch (what stands out more to sysadmins and cybersecurity analysts than empty logs??). The former was a bit harder to write, but also seemed to be too heavy-handed for something as surgical as log tampering. What if you're using stolen credentials to log in as a legitimate user through SSH? Maybe you fat-fingered the password once and ended up in the btmp log, but the admin who's creds you stole forgets his password every couple days. He's created more entries in the btmp log than you have. The goal of log tampering is to restore the logs to the same state they were in before you interacted with the system, but wiping all instances of that user is cleaning a mess you didn't make, and creating a log state that didn't previously exist. TRACEERASE avoids this by allowing the user to clean individual lines from logs, making sure you remove your IOCs, and nothing else.

## How it works

TRACEERASE has two modes, individual and automated. Individual allows you to specify an individual file you would like to clean. Automated will determine the OS and then use branching logic to find logs on the system that can be cleaned (this mode is still under development). Once a log file is identified, it will be opened in the log viewer for viewing and dirty line selection,  and then automatically cleaned and timestomped.

### Custom Log Viewer

Full disclosure, I can't take full credit for this (or even most of it), I pulled a lot of code from [tdryer's excellent curses-based text editor](https://github.com/tdryer/editor). There are a number of changes, however, since the intended usage is much different. The <kbd>&#8593;</kbd> and <kbd>&#8595;</kbd> arrow keys are used for cursor movement, along with <kbd>PgUp</kbd> and <kbd>PgDn</kbd> (home and end will be implemented eventually). The <kbd>SPACEBAR</kbd> is used to select or unselect lines. Selected lines are highlighted.

![](img/select_mode.png)*Select Mode*

<kbd>Q</kbd> will quit without saving your selections:
![](img/quit_without_saving.png)*Quitting*

<kbd>ENTER</kbd> will save your selections for cleaning.
![](img/save_selections.png)*Saving*

### utmp Binary Logs

TRACEERASE has a built-in utmp binary reader to avoid relying on any native programs on the target machine. Below is the struct code:
```python
'''
struct utmp {
    short   ut_type;              /* Type of record */
    pid_t   ut_pid;               /* PID of login process */
    char    ut_line[UT_LINESIZE]; /* Device name of tty - "/dev/" */
    char    ut_id[4];             /* Terminal name suffix, or inittab(5) ID */
    char    ut_user[UT_NAMESIZE]; /* Username */
    char    ut_host[UT_HOSTSIZE]; /* Hostname for remote login, or kernel version for run-level messages */
    struct  exit_status ut_exit;  /* Exit status of a process marked as DEAD_PROCESS; not used by Linux init (1 */
    /* The ut_session and ut_tv fields must be the same size when compiled 32- and 64-bit. 
       This allows data files and shared memory to be shared between 32- and 64-bit applications. */
    #if __WORDSIZE == 64 && defined __WORDSIZE_COMPAT32
    int32_t ut_session;           /* Session ID (getsid(2)), used for windowing */
    struct {
        int32_t tv_sec;           /* Seconds */
        int32_t tv_usec;          /* Microseconds */
    } ut_tv;                      /* Time entry was made */
    #else
    long   ut_session;           /* Session ID */
    struct timeval ut_tv;        /* Time entry was made */
    #endif
    int32_t ut_addr_v6[4];        /* Internet address of remote host; IPv4 address uses just ut_addr_v6[0] */
    char __unused[20];            /* Reserved for future use */
};
'''
STRUCT = struct.Struct('hi32s4s32s256shhiii4i20s')
```

## ctime Manipulation

The `debugfs` tool, usually installed on ext2-4 filesystems, can manipulate the ctime stored in the inode table. From what I've seen in testing, you shouldn't attempt this if the file is opened by another process; it seems you can't flush the cached inode table (which is the ctime you see in the `stat` command) with the value placed there by `debugfs` if a process has a handle on the file.

## Meme
For those who read the whole README, here's a meme:

Intern: How did you know the hacker was from Bel-Air?  
IR Analyst:  
Because he left...  
( •\_•)  
( •\_•)>⌐■-■  
fresh prints  
(⌐■\_■)  
YYYYEEEEEEEEEAAAAAAAAHHHHHHHHHHHHH  

