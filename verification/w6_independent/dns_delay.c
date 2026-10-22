/* Process-local resolver latency fixture. It delays, but never invents resolver answers. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <netdb.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

int getaddrinfo(const char *node, const char *service,
                const struct addrinfo *hints, struct addrinfo **result) {
    int (*real_getaddrinfo)(const char *, const char *, const struct addrinfo *, struct addrinfo **);
    *(void **)(&real_getaddrinfo) = dlsym(RTLD_NEXT, "getaddrinfo");
    if (!real_getaddrinfo) return EAI_SYSTEM;
    if (node && strcmp(node, "w6-stall.invalid") == 0) {
        const char *trace = getenv("W6_DNS_DELAY_TRACE");
        if (trace) {
            int descriptor = open(trace, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, 0600);
            if (descriptor >= 0) { (void)write(descriptor, "D", 1); (void)close(descriptor); }
        }
        struct timespec delay = {.tv_sec = 8, .tv_nsec = 0};
        while (nanosleep(&delay, &delay) != 0) {}
    }
    return real_getaddrinfo(node, service, hints, result);
}
