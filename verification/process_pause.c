#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

/* Verification only: pause one disposable child's real SQLite commit before
 * or after actual journal unlink. No SQLite return value is substituted.
 * Parent either kills that child or releases it; the pause also expires.
 * https://www.sqlite.org/atomiccommit.html#_deleting_the_rollback_journal
 */
static atomic_int fired = 0;

static void pause_commit(const char *phase) {
    const char *marker = getenv("DIAMOND_PAUSE_MARKER");
    const char *release = getenv("DIAMOND_PAUSE_RELEASE");
    if (!marker || !release) return;
    int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return;
    (void)write(fd, phase, strlen(phase));
    (void)close(fd);
    const struct timespec delay = {.tv_sec = 0, .tv_nsec = 10000000};
    for (int i = 0; i < 1000 && access(release, F_OK) != 0; i++) {
        (void)nanosleep(&delay, NULL);
    }
}

int unlink(const char *path) {
    const char *journal = getenv("DIAMOND_PAUSE_JOURNAL");
    const char *arm = getenv("DIAMOND_PAUSE_ARM");
    const char *phase = getenv("DIAMOND_PAUSE_PHASE");
    int trigger = journal && arm && phase && strcmp(journal, path) == 0 &&
                  access(arm, F_OK) == 0 && !atomic_exchange(&fired, 1);
    if (trigger && strcmp(phase, "before") == 0) pause_commit("before");
    int result = (int)syscall(SYS_unlink, path);
    int saved = errno;
    if (trigger && result == 0 && strcmp(phase, "after") == 0) pause_commit("after");
    errno = saved;
    return result;
}
