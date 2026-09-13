#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

/* This launcher changes only its owned child process environment. The actual
 * Elixir copy runs unchanged; the kernel interrupts its real 1,280-byte write. */
int main(int argc, char **argv) {
    struct sigaction action = {0};
    struct rlimit core = {0, 0};
    struct rlimit size = {1024, 1024};
    sigset_t unblocked;

    if (argc < 3 || (strcmp(argv[1], "kill") != 0 && strcmp(argv[1], "error") != 0)) {
        fputs("usage: limited_copy kill|error executable [arguments...]\n", stderr);
        return 125;
    }

    /* Shells cannot reset a signal ignored when they started. Reset it here,
     * including the blocking mask inherited through the test runner. */
    action.sa_handler = strcmp(argv[1], "kill") == 0 ? SIG_DFL : SIG_IGN;
    if (sigemptyset(&action.sa_mask) != 0 ||
        sigaction(SIGXFSZ, &action, NULL) != 0 ||
        sigemptyset(&unblocked) != 0 ||
        sigaddset(&unblocked, SIGXFSZ) != 0 ||
        sigprocmask(SIG_UNBLOCK, &unblocked, NULL) != 0 ||
        setrlimit(RLIMIT_CORE, &core) != 0 ||
        setrlimit(RLIMIT_FSIZE, &size) != 0) {
        perror("configure limited copy child");
        return 125;
    }

    printf("COPY_LIMIT mode=%s bytes=1024 signal=%d\n", argv[1], SIGXFSZ);
    if (fflush(stdout) != 0) {
        perror("report limited copy child");
        return 125;
    }
    execv(argv[2], &argv[2]);
    perror("execute limited copy child");
    return 125;
}
