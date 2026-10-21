/* Test-only Linux clock shim; never changes an operating-system clock. */
#define _GNU_SOURCE
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

int clock_gettime(clockid_t clock, struct timespec *result) {
    int status = (int)syscall(SYS_clock_gettime, clock, result);
    if (status != 0 || clock != CLOCK_REALTIME) {
        return status;
    }
    const char *marker = getenv("DIAMOND_CLOCK_MARKER");
    const char *offset = getenv("DIAMOND_CLOCK_OFFSET");
    if (marker != NULL && offset != NULL && access(marker, F_OK) == 0) {
        if (strcmp(offset, "3600") == 0) {
            result->tv_sec += 3600;
        } else if (strcmp(offset, "-3600") == 0) {
            result->tv_sec -= 3600;
        }
    }
    return status;
}
