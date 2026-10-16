// 백엔드 CLI 호출. 앱은 화면만 맡고, 설정 쓰기·검증·판단은 모두 백엔드가 한다(파일을 직접 쓰지 않는다).
// 배포 앱: 번들 안의 Contents/MacOS/askproject-backend(런처 → 내장 파이썬).
// 개발 앱(scripts/build-macos-app.sh): Info.plist의 레포·uv 경로로 `uv run --project <repo> askproject`.

import Foundation

enum Backend {
    /// 앱↔백엔드 계약 버전. 백엔드 `askproject.__protocol__`과 다르면 업데이트가 반쯤 된 상태다.
    static let expectedProtocol = 1

    static var bundledLauncher: URL? {
        let u = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/askproject-backend")
        return FileManager.default.isExecutableFile(atPath: u.path) ? u : nil
    }

    static var isBundled: Bool { bundledLauncher != nil }

    static var devRepo: String {
        (Bundle.main.object(forInfoDictionaryKey: "AskProjectRepo") as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? NSString(string: "~/Documents/projects/askproject").expandingTildeInPath
    }

    static var uv: String {
        if let v = Bundle.main.object(forInfoDictionaryKey: "AskProjectUV") as? String, !v.isEmpty { return v }
        for p in ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"] where FileManager.default.isExecutableFile(atPath: p) {
            return p
        }
        return "/opt/homebrew/bin/uv"
    }

    struct Output {
        let code: Int32
        let out: String
        let err: String

        var ok: Bool { code == 0 }
        /// 사람이 읽을 마지막 오류 한 줄(파이썬 traceback이면 마지막 줄이 원인)
        var errorLine: String {
            let lines = (err.isEmpty ? out : err).split(separator: "\n").map(String.init).filter { !$0.isEmpty }
            return lines.last ?? "알 수 없는 오류 (종료 코드 \(code))"
        }
    }

    /// 셸을 거치지 않고, 셸 환경(ANTHROPIC_* 등)을 물려주지 않는다. input은 표준 입력(비밀값은 인자로 넘기지 않는다).
    static func run(_ args: [String], input: Data? = nil) async -> Output {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                var env = ["HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
                           "USER": NSUserName(), "LOGNAME": NSUserName()]
                if let tmp = ProcessInfo.processInfo.environment["TMPDIR"] { env["TMPDIR"] = tmp }
                if let launcher = bundledLauncher {
                    p.executableURL = launcher
                    p.arguments = args
                } else {
                    p.executableURL = URL(fileURLWithPath: uv)
                    p.arguments = ["run", "--project", devRepo, "askproject"] + args
                    p.currentDirectoryURL = URL(fileURLWithPath: devRepo)
                    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
                    env["PYTHONUNBUFFERED"] = "1"
                }
                p.environment = env
                let out = Pipe(), err = Pipe(), inp = Pipe()
                p.standardOutput = out
                p.standardError = err
                p.standardInput = input == nil ? FileHandle.nullDevice : inp
                do {
                    try p.run()
                } catch {
                    cont.resume(returning: Output(code: -1, out: "", err: "실행 실패: \(error.localizedDescription)"))
                    return
                }
                if let input {
                    inp.fileHandleForWriting.write(input)
                    try? inp.fileHandleForWriting.close()
                }
                // 표준 출력·오류를 동시에 읽는다(한쪽 파이프가 가득 차 멈추지 않게)
                var errData = Data()
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global().async {
                    errData = err.fileHandleForReading.readDataToEndOfFile()
                    group.leave()
                }
                let outData = out.fileHandleForReading.readDataToEndOfFile()
                group.wait()
                p.waitUntilExit()
                cont.resume(returning: Output(code: p.terminationStatus, out: String(decoding: outData, as: UTF8.self),
                                              err: String(decoding: errData, as: UTF8.self)))
            }
        }
    }

    /// JSON 출력 명령. 표준 출력의 마지막 JSON 줄을 디코딩한다(앞에 로그가 섞여도 되게).
    static func json<T: Decodable>(_ type: T.Type, _ args: [String], input: Data? = nil) async -> (T?, Output) {
        let r = await run(args, input: input)
        let line = r.out.split(separator: "\n").last(where: { $0.hasPrefix("{") || $0.hasPrefix("[") }).map(String.init) ?? ""
        return (try? JSONDecoder().decode(T.self, from: Data(line.utf8)), r)
    }

    /// 설정 변경: `askproject config set --json` 표준 입력으로 패치 JSON
    static func setConfig(settings: [String: Any] = [:], secrets: [String: String] = [:],
                          repos: [[String: String]]? = nil) async -> (ok: Bool, error: String?) {
        var patch: [String: Any] = [:]
        if !settings.isEmpty { patch["settings"] = settings }
        if !secrets.isEmpty { patch["secrets"] = secrets }
        if let repos { patch["repos"] = repos }
        guard let data = try? JSONSerialization.data(withJSONObject: patch) else { return (false, "JSON 변환 실패") }
        let (res, out) = await json(ConfigSetResult.self, ["config", "set", "--json"], input: data)
        if let res { return (res.ok, res.error) }
        return (false, out.errorLine)
    }
}
