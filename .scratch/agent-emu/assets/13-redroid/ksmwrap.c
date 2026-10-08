// Run a command with PR_SET_MEMORY_MERGE (KSM for all anon memory), inherited by children.
#include <stdio.h>
#include <sys/prctl.h>
#include <unistd.h>
#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#endif
int main(int c, char **v) {
    if (c < 2) return 2;
    if (prctl(PR_SET_MEMORY_MERGE, 1, 0, 0, 0)) { perror("prctl"); return 1; }
    execvp(v[1], v + 1);
    perror("exec");
    return 1;
}
