#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/*
 * Red-team-only interposer. After the real lstat64 has returned metadata for
 * one exact disposable path, expose a marker and pause until the harness has
 * replaced that path. This makes the permission-check/open race reproducible
 * without probabilistic scheduling or access to a non-test file.
 */
int lstat64(const char *path, struct stat64 *metadata) {
    static int (*real_lstat64)(const char *, struct stat64 *);
    static int paused;
    if (real_lstat64 == NULL) {
        real_lstat64 = dlsym(RTLD_NEXT, "lstat64");
        if (real_lstat64 == NULL) {
            _exit(126);
        }
    }
    int result = real_lstat64(path, metadata);
    const char *target = getenv("DIAMOND_RED_TEAM_RACE_PATH");
    const char *ready = getenv("DIAMOND_RED_TEAM_RACE_READY");
    const char *release = getenv("DIAMOND_RED_TEAM_RACE_RELEASE");
    if (!paused && result == 0 && target != NULL && ready != NULL && release != NULL &&
        strcmp(path, target) == 0) {
        paused = 1;
        int descriptor = open(ready, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (descriptor < 0) {
            _exit(125);
        }
        close(descriptor);
        for (int waits = 0; waits < 10000 && access(release, F_OK) != 0; waits++) {
            usleep(1000);
        }
        if (access(release, F_OK) != 0) {
            _exit(124);
        }
    }
    return result;
}
