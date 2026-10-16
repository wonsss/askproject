// askproject-backend: 앱 번들 안의 파이썬으로 `python -I -B -m askproject <인자>`를 실행한다.
// launchd 에이전트(SMAppService BundleProgram)와 메뉴 막대 앱이 이 실행 파일 하나로 백엔드를 부른다.
// 번들 위치를 자기 경로에서 계산하므로 앱을 옮겨도 동작한다. 셸 환경의 PYTHON*·ANTHROPIC_*는 쓰지 않는다.
#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    char raw[PATH_MAX], exe[PATH_MAX], backend[PATH_MAX], python[PATH_MAX], cafile[PATH_MAX];
    uint32_t size = sizeof(raw);
    if (_NSGetExecutablePath(raw, &size) != 0 || realpath(raw, exe) == NULL) {
        fprintf(stderr, "askproject-backend: 실행 경로를 알 수 없습니다\n");
        return 70;
    }
    const char *override = getenv("ASKPROJECT_BUNDLE_DIR");
    if (override && *override) {
        snprintf(backend, sizeof(backend), "%s", override);
    } else {
        // Contents/MacOS/askproject-backend → Contents/Resources/backend
        snprintf(backend, sizeof(backend), "%s/../Resources/backend", dirname(exe));
    }
    snprintf(python, sizeof(python), "%s/python/bin/python3.12", backend);
    snprintf(cafile, sizeof(cafile), "%s/python/lib/python3.12/site-packages/certifi/cacert.pem", backend);

    setenv("ASKPROJECT_APP", "1", 0);  // 제품 모드(경로·Keychain). 개발 때만 0으로 덮어쓸 수 있다
    setenv("ASKPROJECT_BUNDLE_DIR", backend, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    if (access(cafile, R_OK) == 0) setenv("SSL_CERT_FILE", cafile, 1);
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONHOME");

    char **args = calloc((size_t)argc + 5, sizeof(char *));
    if (!args) return 71;
    int n = 0;
    args[n++] = python;
    args[n++] = "-I";  // 환경변수·사용자 site-packages 무시
    args[n++] = "-B";  // __pycache__를 쓰지 않는다(번들 서명 보존)
    args[n++] = "-m";
    args[n++] = "askproject";
    for (int i = 1; i < argc; i++) args[n++] = argv[i];
    args[n] = NULL;
    execv(python, args);
    fprintf(stderr, "askproject-backend: %s 실행 실패: %s\n", python, strerror(errno));
    return 72;
}
