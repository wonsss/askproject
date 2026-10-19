// 메뉴 막대 앱의 상태. 15초마다 `askproject status --json`(Claude 호출 없음, 비용 0)을 읽는다.

import AppKit
import SwiftUI
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    @Published var status: AppStatus?
    @Published var paths: BackendPaths?
    @Published var busy: String?
    @Published var message: String?
    @Published var claude: ClaudeCheck?
    @Published var consoleUp = false
    @Published var consolePortTaken = false  // 콘솔 포트에 다른 프로그램이 응답함

    private var timer: Timer?
    private var userToggled = false

    var isApp: Bool { Backend.isBundled }

    var health: Health {
        guard let s = status, let bot = s.jobs["bot"] else { return .unknown }
        switch bot.state {
        case "running": return .running
        case "waiting": return .restarting
        default: return .off
        }
    }

    /// 앱과 백엔드 계약 버전이 다르면(업데이트가 반쯤 된 상태) 경고한다
    var protocolMismatch: Bool {
        guard let p = status?.protocol else { return false }
        return p != Backend.expectedProtocol
    }

    /// 배너로 보여 줄 경고(가장 급한 것부터)
    var warnings: [String] {
        guard let s = status else { return [] }
        var w: [String] = []
        if protocolMismatch { w.append(L("앱과 백엔드 버전이 달라요. 앱을 다시 설치해 주세요.")) }
        if let c = s.host_conflict, !c.others.isEmpty {
            w.append(L("다른 맥(%@)에서 같은 슬랙 봇이 켜져 있어요. 한 대만 켜 두세요.", c.others.map(\.host_name).joined(separator: ", ")))
        }
        if isApp && Agents.needsApproval { w.append(L("시스템 설정 → 로그인 항목에서 AskProject를 허용해야 봇이 켜져요.")) }
        if let t = s.github_token, t.warning, let d = t.days_left {
            w.append(d <= 0 ? L("GitHub 토큰이 만료됐어요. 새 토큰을 발급해 설정에서 바꿔 주세요.")
                            : L("GitHub 토큰이 %ld일 뒤 만료돼요. 미리 새 토큰으로 바꿔 주세요.", Int(d.rounded(.up))))
        }
        if s.configured == true && (s.allowed_channels ?? []).isEmpty {
            w.append(L("답할 채널이 정해지지 않았어요. 봇이 초대된 모든 채널에서 코드 내용을 답해요."))
        }
        if let p = s.power, p.mode != "default", p.autologin_user == nil {
            w.append(p.filevault_on ? L("FileVault가 켜져 있어 재부팅 후 로그인 전까지 봇이 멈춰요.")
                                    : L("자동 로그인이 꺼져 있어 재부팅 후 로그인 전까지 봇이 멈춰요."))
        }
        return w
    }

    init() {
        refresh()
        loadPaths()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func loadPaths() {
        Task {
            let (p, _) = await Backend.json(BackendPaths.self, ["paths", "--json"])
            paths = p
        }
    }

    func refresh() {
        Task {
            let (s, r) = await Backend.json(AppStatus.self, ["status", "--json"])
            guard let s else {
                message = L("상태를 읽지 못했어요: ") + r.errorLine
                return
            }
            let wasRunning = health == .running
            status = s
            probeConsole()
            if wasRunning && health != .running && !userToggled {
                notify(L("AskProject 봇이 멈췄어요"), L("자동으로 다시 띄우는 중이에요. 계속되면 로그를 확인하세요."))
            }
            userToggled = false
        }
    }

    func toggle() {
        let turnOn = health == .off || health == .unknown
        userToggled = true
        if isApp {
            busy = turnOn ? L("켜는 중") : L("끄는 중")
            Task {
                do {
                    if turnOn {
                        _ = await Backend.run(["migrate"])  // 업데이트 뒤 첫 시작이면 스키마·설정을 먼저 올린다
                        let approval = try Agents.enableAll()
                        message = approval ? L("시스템 설정 → 로그인 항목에서 AskProject를 켜 주세요. 허용하면 바로 시작돼요.")
                                           : L("켰어요. 봇이 슬랙에 연결되기까지 10초쯤 걸려요.")
                    } else {
                        try Agents.disableAll()
                        message = L("껐어요. 잠자기 방지도 풀렸어요.")
                    }
                } catch {
                    message = L("실패: %@", error.localizedDescription)
                }
                busy = nil
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                refresh()
            }
            return
        }
        perform(turnOn ? L("켜는 중") : L("끄는 중"), ["host", turnOn ? "on" : "off"]) { r in
            r.ok ? (turnOn ? L("켰어요. 봇이 슬랙에 연결되기까지 10초쯤 걸려요.") : L("껐어요. 잠자기 방지도 풀렸어요."))
                 : L("실패: ") + r.errorLine
        }
    }

    /// 설정을 바꾼 뒤 실행 중인 봇에 적용한다
    func restartBotIfRunning() {
        guard health == .running || health == .restarting else { return }
        if isApp {
            Agents.restart("bot")
        } else {
            // host on은 plist를 이 앱의 레포로 다시 쓴다 — 다른 체크아웃의 개발 앱이면 실제 봇이 옮겨 간다.
            // restart는 등록된 작업만 kickstart하고, 다른 레포에 등록돼 있으면 건드리지 않고 이유를 돌려준다.
            Task {
                let r = await Backend.run(["host", "restart", "bot"])
                if !r.ok { message = L("봇을 재시작하지 않았어요: ") + r.errorLine }
            }
        }
    }

    func setProvider(_ mode: String) {
        guard mode != status?.provider, !isApp else { return }  // 앱(제품)에서는 구독 모드를 쓰지 않는다
        perform(L("인증 경로 바꾸는 중"), ["provider", mode]) { r in
            r.ok ? (r.out.split(separator: "\n").first.map(String.init) ?? L("바꿨어요.")) : L("전환 실패: ") + r.errorLine
        }
    }

    func setPower(_ mode: String) {
        perform(L("절전 모드 바꾸는 중"), ["power", mode]) { r in
            r.ok ? (r.out.split(separator: "\n").first.map(String.init) ?? L("바꿨어요.")) : L("실패: ") + r.errorLine
        }
    }

    func backupNow() {
        perform(L("백업 중"), ["backup"]) { r in
            r.ok ? (r.out.split(separator: "\n").last.map(String.init) ?? L("백업 완료")) : L("백업 실패: ") + r.errorLine
        }
    }

    func checkClaude() {
        busy = L("크레딧 확인 중")
        Task {
            let (s, r) = await Backend.json(AppStatus.self, ["status", "--json", "--check-claude"])
            busy = nil
            if let s {
                status = s
                claude = s.claude
                message = s.claude?.ok == true ? L("Claude API 키·크레딧 정상") : L("Claude 호출 불가: %@", s.claude?.problem ?? "-")
            } else {
                message = L("확인 실패: ") + r.errorLine
            }
        }
    }

    func diagnostics() {
        busy = L("진단 묶음 만드는 중")
        Task {
            let (d, r) = await Backend.json(DiagnosticsResult.self, ["diagnostics"])
            busy = nil
            if let d {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: d.path)])
                message = L("진단 묶음을 만들었어요(비밀값은 가렸어요). 레포 이름·채널 ID는 들어 있으니 받는 사람을 확인하고 보내세요.")
            } else {
                message = L("진단 묶음 실패: ") + r.errorLine
            }
        }
    }

    private func perform(_ label: String, _ args: [String], done: @escaping (Backend.Output) -> String) {
        busy = label
        Task {
            let r = await Backend.run(args)
            busy = nil
            message = done(r)
            refresh()
        }
    }

    private func probeConsole() {
        Task { await updateConsoleState() }
    }

    enum ConsoleProbe { case up, down, portTaken }

    /// 응답이 와도 콘솔의 식별 헤더(X-AskProject-Console)가 없으면 같은 포트를 다른 프로그램이 쓰는 것이다.
    private func probeConsoleState() async -> ConsoleProbe {
        guard let base = status?.console_url, let url = URL(string: base) else { return .down }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        guard let (_, resp) = try? await URLSession.shared.data(for: req) else { return .down }
        let ours = (resp as? HTTPURLResponse)?.value(forHTTPHeaderField: "X-AskProject-Console") != nil
        return ours ? .up : .portTaken  // 401도 콘솔의 응답이다(켜져 있음)
    }

    @discardableResult
    private func updateConsoleState() async -> ConsoleProbe {
        let state = await probeConsoleState()
        consoleUp = state == .up
        consolePortTaken = state == .portTaken
        return state
    }

    /// 콘솔은 실행마다 새 키를 쓴다. 키를 붙여 열면 콘솔이 쿠키로 바꾸고 주소에서 지운다.
    func openConsole(_ path: String = "/") {
        Task {
            let state = await updateConsoleState()
            if state == .portTaken {
                let port = status.flatMap { URL(string: $0.console_url)?.port }.map(String.init) ?? "18787"
                message = L("%@ 포트를 다른 프로그램이 쓰고 있어 웹 콘솔이 뜨지 못했어요. 그 프로그램을 끈 뒤 콘솔을 다시 켜세요.", port)
                return
            }
            guard state == .up, let s = status else {
                message = L("웹 콘솔이 꺼져 있어요. '켜기'로 켜거나 잠시 뒤 다시 시도하세요.")
                return
            }
            guard var comps = URLComponents(string: s.console_url + path) else { return }
            if let key = s.console_key {
                let items = comps.queryItems ?? []
                comps.queryItems = items + [URLQueryItem(name: "key", value: key)]
            }
            if let url = comps.url { NSWorkspace.shared.open(url) }
        }
    }

    func openLogs() {
        let dir = paths?.log_dir ?? Backend.devRepo + "/data/logs"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(URL(fileURLWithPath: dir))
    }

    /// 백업 폴더(`paths --json`의 backup_dir). 아직 백업이 없어 폴더가 없으면 만들어서 연다.
    func openBackups() {
        let dir = paths?.backup_dir ?? Backend.devRepo + "/backups"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(URL(fileURLWithPath: dir))
    }

    private func notify(_ title: String, _ body: String) {
        let c = UNMutableNotificationContent()
        c.title = title
        c.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }
}
