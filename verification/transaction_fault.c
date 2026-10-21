#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

/* Linux-only replay shim, loaded into the disposable Diamond child only.
 * SQLite DELETE-journal commit occurs at journal deletion:
 * https://sqlite.org/atomiccommit.html#_deleting_the_rollback_journal
 * Perform that real deletion, then lose its acknowledgment exactly once.
 * Exact path plus an explicit arm file prevents startup/schema faults.
 */
static atomic_int fired = 0;

int unlink(const char *path) {
    const char *journal = getenv("DIAMOND_FAULT_JOURNAL");
    const char *arm = getenv("DIAMOND_FAULT_ARM");
    int trigger = journal && arm && strcmp(path, journal) == 0 &&
                  access(arm, F_OK) == 0 && !atomic_exchange(&fired, 1);
    int result = (int)syscall(SYS_unlink, path);
    int saved_errno = errno;
    if (trigger && result == 0) {
        const char *log = getenv("DIAMOND_FAULT_LOG");
        if (log) {
            int fd = open(log, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
            if (fd >= 0) {
                const char event[] = "unlink committed journal; returned EIO\n";
                (void)write(fd, event, sizeof(event) - 1);
                (void)close(fd);
            }
        }
        errno = EIO;
        return -1;
    }
    errno = saved_errno;
    return result;
}
