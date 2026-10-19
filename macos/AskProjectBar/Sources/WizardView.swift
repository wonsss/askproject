// 첫 실행 마법사: 슬랙 앱 → GitHub 토큰·레포 → Claude API 키 → 답할 채널 → 점검 → 켜기 → 팀 공유(선택).
// 진행 단계는 저장해 두고 이어서 한다(슬랙 앱 승인이 며칠 걸릴 수 있다). 값 저장·검증은 모두 백엔드가 한다.

import AppKit
import SwiftUI

@MainActor
final class WizardModel: ObservableObject {
    enum Step: Int, CaseIterable {
        case welcome, slack, github, claude, channels, verify, start, share  // 저장된 단계 번호가 바뀌지 않게 끝에만 붙인다

        var title: String {
            [L("시작"), L("슬랙 앱"), "GitHub", "Claude", L("답할 채널"), L("점검"), L("켜기"), L("팀 공유 (선택)")][rawValue]
        }
    }

    @Published var step: Step { didSet { UserDefaults.standard.set(step.rawValue, forKey: "wizard.step") } }
    @Published var slackPending: Bool { didSet { UserDefaults.standard.set(slackPending, forKey: "wizard.slackPending") } }
    @Published var working: String?
    @Published var note: CheckResult?
    @Published var config: ConfigState?

    @Published var botToken = ""
    @Published var appToken = ""
    @Published var githubOwner = ""
    @Published var githubToken = ""
    @Published var repos: [GitRepo] = []
    /// 고른 레포(full_name → 읽을 브랜치, 비우면 기본 브랜치). 여러 개를 고를 수 있다
    @Published var selected: [String: String] = [:]
    @Published var apiKey = ""
    @Published var channels: [SlackChannel] = []
    @Published var allowed: Set<String> = []
    @Published var allowDM = false
    @Published var hostChannel = ""
    @Published var checks: [DoctorItem] = []
    @Published var started = false

    init() {
        step = Step(rawValue: UserDefaults.standard.integer(forKey: "wizard.step")) ?? .welcome
        slackPending = UserDefaults.standard.bool(forKey: "wizard.slackPending")
    }

    func secretSaved(_ key: String) -> Bool { config?.secrets[key] ?? false }

    static func prefixProblem(_ value: String, _ prefix: String, _ what: String) -> String? {
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if v.isEmpty { return nil }
        if v.contains(" ") { return L("%@에 공백이 들어 있어요", what) }
        return v.hasPrefix(prefix) ? nil : L("%@은 '%@'로 시작해야 해요", what, prefix)
    }

    var botTokenProblem: String? { Self.prefixProblem(botToken, "xoxb-", L("봇 토큰")) }
    var appTokenProblem: String? {
        if appToken.hasPrefix("xoxb-") { return L("봇 토큰이 아니라 앱 수준 토큰(xapp-)을 넣어 주세요") }
        return Self.prefixProblem(appToken, "xapp-", L("앱 토큰"))
    }
    var githubTokenProblem: String? {
        let v = githubToken.trimmingCharacters(in: .whitespaces)
        if v.hasPrefix("ghp_") { return nil }  // classic도 받지만 fine-grained를 권한다
        return Self.prefixProblem(v, "github_pat_", L("GitHub 토큰"))
    }
    var apiKeyProblem: String? { Self.prefixProblem(apiKey, "sk-ant-", L("API 키")) }

    func load() async {
        let (c, _) = await Backend.json(ConfigState.self, ["config", "get", "--json"])
        config = c
        guard let c else { return }
        let ids = (c.settings.allowed_channels ?? "").split(separator: ",").map(String.init)
        allowed = Set(ids.filter { $0 != "dm" })
        allowDM = ids.contains("dm")
        hostChannel = c.settings.host_channel ?? c.settings.escalation_channel ?? ""
    }

    func go(_ s: Step) {
        note = nil
        step = s
    }

    private func busy(_ label: String, _ work: () async -> Void) async {
        working = label
        await work()
        working = nil
    }

    // MARK: 단계별 동작

    func openSlackCreate() async {
        let (u, _) = await Backend.json(URLResult.self, ["setup", "manifest-url"])
        if let u, let url = URL(string: u.url) { NSWorkspace.shared.open(url) }
    }

    func copyAdminRequest() {
        let text = [
            L("안녕하세요. 기획자가 슬랙에서 코드·기획 문서를 근거로 질문할 수 있는 'AskProject' 봇을 설치하려고 해요."),
            L("방금 만든 슬랙 앱(AskProject)의 워크스페이스 설치 승인을 부탁드려요."),
            L("- 봇은 멘션·DM에만 답하고, 허용한 채널에서만 동작해요."),
            L("- 요청 권한: 멘션·DM 읽기, 메시지 쓰기, 파일 읽기·쓰기(봇이 만든 것만), 공개 채널 기록 읽기(선택 기능)"),
        ].joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        note = CheckResult(ok: true, detail: L("관리자에게 보낼 요청 문구를 복사했어요. 승인되면 이 화면으로 돌아오세요."), step: nil)
    }

    func saveSlack() async {
        await busy(L("슬랙 연결 확인 중")) {
            var secrets: [String: String] = [:]
            if !botToken.isEmpty { secrets["ASKPROJECT_SLACK_BOT_TOKEN"] = botToken.trimmingCharacters(in: .whitespaces) }
            if !appToken.isEmpty { secrets["ASKPROJECT_SLACK_APP_TOKEN"] = appToken.trimmingCharacters(in: .whitespaces) }
            if !secrets.isEmpty {
                let r = await Backend.setConfig(secrets: secrets)
                if !r.ok { note = CheckResult(ok: false, detail: r.error ?? L("저장 실패"), step: "slack"); return }
            }
            let (c, out) = await Backend.json(CheckResult.self, ["setup", "check", "slack"])
            note = c ?? CheckResult(ok: false, detail: out.errorLine, step: "slack")
            if c?.ok == true {
                botToken = ""; appToken = ""
                slackPending = false
                await load()
            }
        }
    }

    func openPATCreate() async {
        var args = ["setup", "pat-url"]
        let owner = githubOwner.trimmingCharacters(in: .whitespaces)
        if !owner.isEmpty { args += ["--owner", owner] }
        let (u, _) = await Backend.json(URLResult.self, args)
        if let u, let url = URL(string: u.url) { NSWorkspace.shared.open(url) }
    }

    func saveGitHubToken() async {
        await busy(L("레포 목록 불러오는 중")) {
            if !githubToken.isEmpty {
                let r = await Backend.setConfig(secrets: ["ASKPROJECT_GITHUB_TOKEN": githubToken.trimmingCharacters(in: .whitespaces)])
                if !r.ok { note = CheckResult(ok: false, detail: r.error ?? L("저장 실패"), step: "github"); return }
                githubToken = ""
                await load()
            }
            let (list, out) = await Backend.json(RepoList.self, ["setup", "repos"])
            repos = list?.repos ?? []
            for current in config?.repos ?? [] {  // 이미 등록한 레포는 체크해 둔다
                if let r = repos.first(where: { $0.url.lowercased() == current.url.lowercased() }), selected[r.full_name] == nil {
                    selected[r.full_name] = current.ref == r.default_branch ? "" : current.ref
                }
            }
            note = list.map { CheckResult(ok: $0.ok, detail: $0.ok ? L("읽을 수 있는 레포 %@", $0.detail) : $0.detail, step: "github") }
                ?? CheckResult(ok: false, detail: out.errorLine, step: "github")
        }
    }

    /// 고른 레포를 저장 순서대로. 이미 등록한 레포가 앞(첫 번째가 기본 레포), 새로 고른 레포는 목록 순서대로 뒤에 붙는다.
    var selectedRepos: [GitRepo] {
        let picked = repos.filter { selected[$0.full_name] != nil }
        let order = (config?.repos ?? []).map { $0.url.lowercased() }
        return picked.sorted { a, b in
            (order.firstIndex(of: a.url.lowercased()) ?? Int.max) < (order.firstIndex(of: b.url.lowercased()) ?? Int.max)
        }
    }

    func toggle(_ r: GitRepo, _ on: Bool) {
        if on { selected[r.full_name] = selected[r.full_name] ?? "" } else { selected[r.full_name] = nil }
    }

    func saveRepo() async {
        let picked = selectedRepos
        guard !picked.isEmpty else { return }
        await busy(L("레포 확인 중")) {
            let rows = picked.map { r -> [String: String] in
                let ref = (selected[r.full_name] ?? "").trimmingCharacters(in: .whitespaces)
                return ["name": r.name, "url": r.url, "ref": ref.isEmpty ? r.default_branch : ref, "description": r.description]
            }
            let res = await Backend.setConfig(repos: rows)
            if !res.ok { note = CheckResult(ok: false, detail: res.error ?? L("저장 실패"), step: "repos"); return }
            let (c, out) = await Backend.json(CheckResult.self, ["setup", "check", "github"])
            note = c ?? CheckResult(ok: false, detail: out.errorLine, step: "github")
            await load()
        }
    }

    func saveClaude() async {
        await busy(L("Claude 확인 중 (약 $0.00001)")) {
            if !apiKey.isEmpty {
                let r = await Backend.setConfig(secrets: ["ANTHROPIC_API_KEY": apiKey.trimmingCharacters(in: .whitespaces)])
                if !r.ok { note = CheckResult(ok: false, detail: r.error ?? L("저장 실패"), step: "claude"); return }
                apiKey = ""
            }
            let (c, out) = await Backend.json(CheckResult.self, ["setup", "check", "claude"])
            note = c ?? CheckResult(ok: false, detail: out.errorLine, step: "claude")
            await load()
        }
    }

    func loadChannels() async {
        await busy(L("봇이 들어간 채널 불러오는 중")) {
            let (list, out) = await Backend.json(ChannelList.self, ["setup", "channels"])
            channels = list?.channels ?? []
            note = list.map { CheckResult(ok: $0.ok, detail: $0.ok ? L("봇이 들어간 채널 %@", $0.detail) : $0.detail, step: "channels") }
                ?? CheckResult(ok: false, detail: out.errorLine, step: "channels")
        }
    }

    func saveChannels() async -> Bool {
        var ids = Array(allowed).sorted()
        if allowDM { ids.append("dm") }
        guard !ids.isEmpty else {
            note = CheckResult(ok: false, detail: L("답할 채널을 하나 이상 고르거나 DM을 허용하세요"), step: "channels")
            return false
        }
        let r = await Backend.setConfig(settings: ["allowed_channels": ids.joined(separator: ","),
                                                   "host_channel": hostChannel])
        note = r.ok ? nil : CheckResult(ok: false, detail: r.error ?? L("저장 실패"), step: "channels")
        return r.ok
    }

    func runDoctor() async {
        await busy(L("연결까지 점검 중")) {
            let (items, out) = await Backend.json([DoctorItem].self, ["doctor", "--online", "--json"])
            checks = items ?? []
            if items == nil { note = CheckResult(ok: false, detail: out.errorLine, step: nil) }
        }
    }

    var blocking: [DoctorItem] { checks.filter { $0.status == "fail" && $0.required } }

    func start() async {
        await busy(L("레포를 받아 색인하는 중 (처음엔 몇 분 걸려요)")) {
            let sync = await Backend.run(["sync", "--no-external"])
            if !sync.ok {
                note = CheckResult(ok: false, detail: L("레포 동기화 실패: ") + sync.errorLine, step: "github")
                return
            }
            if Backend.isBundled {
                do {
                    let approval = try Agents.enableAll()
                    note = CheckResult(ok: !approval, detail: approval
                        ? L("시스템 설정 → 로그인 항목에서 AskProject를 허용해 주세요. 허용하면 봇이 바로 시작돼요.")
                        : L("봇을 켰어요. 허용한 채널에서 @AskProject로 질문해 보세요."), step: nil)
                } catch {
                    note = CheckResult(ok: false, detail: L("켜기 실패: %@", error.localizedDescription), step: nil)
                    return
                }
            } else {
                let r = await Backend.run(["host", "on"])
                note = CheckResult(ok: r.ok, detail: r.ok ? L("봇을 켰어요(개발 모드).") : r.errorLine, step: nil)
            }
            started = true
            UserDefaults.standard.set(true, forKey: "wizard.done")
            AppModel.shared.refresh()
        }
    }
}

struct WizardView: View {
    @StateObject private var m = WizardModel()
    @StateObject private var share = SettingsModel()
    @AppStorage(Privacy.ackKey) private var privacyAck = false
    @ObservedObject private var lang = Localizer.shared

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(alignment: .leading, spacing: 14) {
                content
                Spacer(minLength: 0)
                if let n = m.note { NoteView(note: n) }
                footer
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(width: 720, height: 540)
        .task { await m.load() }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("AskProject 설정")).font(.headline).padding(.bottom, 6)
            ForEach(WizardModel.Step.allCases, id: \.self) { s in
                HStack(spacing: 8) {
                    Image(systemName: s.rawValue < m.step.rawValue ? "checkmark.circle.fill"
                                    : s == m.step ? "circle.inset.filled" : "circle")
                        .foregroundStyle(s.rawValue <= m.step.rawValue ? Color.accentColor : .secondary)
                    Text(s.title).foregroundStyle(s == m.step ? .primary : .secondary)
                }
                .font(.callout)
            }
            Spacer()
            if m.slackPending {
                Label(L("슬랙 승인 대기 중"), systemImage: "hourglass").font(.caption).foregroundStyle(.orange)
            }
        }
        .padding(20)
        .frame(width: 180, alignment: .topLeading)
        .background(Color.primary.opacity(0.03))
    }

    @ViewBuilder
    private var content: some View {
        switch m.step {
        case .welcome: welcome
        case .slack: slack
        case .github: github
        case .claude: claude
        case .channels: channelsStep
        case .verify: verify
        case .start: startStep
        case .share: shareStep
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Spacer()
                Picker(L("언어"), selection: $lang.choice) {
                    ForEach(AppLanguage.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)
                .fixedSize()
            }
            Text(L("기획자의 슬랙 질문에 코드와 기획 문서로 답하는 봇을 이 맥에 설치해요")).font(.title3.weight(.semibold))
            Text(L("워크스페이스 관리자 한 명이 한 대에만 설치하면 돼요. 기획자는 슬랙에서 @AskProject로 묻기만 해요."))
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6) {
                Label(L("슬랙 앱 만들기 — 워크스페이스에 따라 관리자 승인이 필요해요"), systemImage: "bubble.left.and.bubble.right")
                Label(L("GitHub 토큰 — 고른 레포의 내용 읽기만 허용"), systemImage: "chevron.left.forwardslash.chevron.right")
                Label(L("Claude API 키 — 질문할 때만 비용이 들어요"), systemImage: "key")
                Label(L("답할 채널 고르기 — 코드 내용이 보이는 범위예요"), systemImage: "number")
            }
            .font(.callout)
            Text(L("이 맥은 봇이 켜져 있는 동안 잠들지 않게 설정할 수 있어요(설정 → 맥 운영). 다른 맥에 같은 봇을 또 켜면 질문이 나뉘니 한 대만 켜 두세요."))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            PrivacySummaryCard()
            Toggle(L("데이터가 Anthropic Claude API와 내가 연결한 서비스로만 간다는 것을 이해했어요"), isOn: $privacyAck)
                .toggleStyle(.checkbox).font(.callout)
        }
    }

    private var slack: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("1. 슬랙 앱 만들기")).font(.title3.weight(.semibold))
            Text(L("버튼을 누르면 설정이 미리 채워진 슬랙 앱 생성 화면이 열려요. 워크스페이스를 고르고 'Create'를 누르세요."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(L("슬랙 앱 만들기 열기")) { Task { await m.openSlackCreate() } }.buttonStyle(.borderedProminent)
                Button(L("관리자 요청 문구 복사")) { m.copyAdminRequest() }
                Toggle(L("관리자 승인 대기 중"), isOn: $m.slackPending).toggleStyle(.checkbox)
            }
            Text(L("2. 토큰 두 개 붙여넣기")).font(.headline).padding(.top, 6)
            TokenField(title: L("봇 토큰 — Install App → Bot User OAuth Token"), placeholder: "xoxb-…", text: $m.botToken,
                       problem: m.botTokenProblem, saved: m.secretSaved("ASKPROJECT_SLACK_BOT_TOKEN"))
            TokenField(title: L("앱 토큰 — Basic Information → App-Level Tokens → Generate (connections:write)"),
                       placeholder: "xapp-…", text: $m.appToken, problem: m.appTokenProblem,
                       saved: m.secretSaved("ASKPROJECT_SLACK_APP_TOKEN"))
        }
    }

    private var github: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("GitHub 토큰과 레포")).font(.title3.weight(.semibold))
            Text(L("레포 내용 읽기(contents: read)만 있는 토큰을 만들어요. 화면이 미리 채워져 열리고, 'Repository access'에서 봇이 읽을 레포만 고르면 돼요."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField(L("레포 소유 조직(예: my-org, 개인이면 비움)"), text: $m.githubOwner).frame(width: 260)
                Button(L("토큰 만들기 열기")) { Task { await m.openPATCreate() } }.buttonStyle(.borderedProminent)
            }
            Text(L("조직이 토큰 승인을 요구하면 조직 관리자가 승인해야 레포가 보여요. 토큰은 90일 뒤 만료되고, 7일 전부터 메뉴 막대에 알려 드려요."))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .top) {
                TokenField(title: L("토큰"), placeholder: "github_pat_…", text: $m.githubToken, problem: m.githubTokenProblem,
                           saved: m.secretSaved("ASKPROJECT_GITHUB_TOKEN"))
                Button(L("레포 불러오기")) { Task { await m.saveGitHubToken() } }
                    .padding(.top, 18).disabled(m.githubTokenProblem != nil || m.working != nil)
            }
            if !m.repos.isEmpty {
                Text(L("봇이 읽을 레포를 모두 고르세요. 여러 개를 고르면 함께 찾아 답해요(예: 웹·앱·서버 레포)."))
                    .font(.callout).foregroundStyle(.secondary)
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(m.repos) { r in
                            HStack {
                                Toggle(r.full_name + (r.private ? " 🔒" : ""), isOn: Binding(
                                    get: { m.selected[r.full_name] != nil }, set: { m.toggle(r, $0) }))
                                Spacer()
                                if m.selected[r.full_name] != nil {
                                    TextField(r.default_branch, text: Binding(
                                        get: { m.selected[r.full_name] ?? "" }, set: { m.selected[r.full_name] = $0 }))
                                        .frame(width: 110).help(L("읽을 브랜치. 비우면 기본 브랜치"))
                                }
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }
                .frame(maxHeight: 190)
                HStack {
                    if let first = m.selectedRepos.first, m.selectedRepos.count > 1 {
                        Text(L("기본 레포: %@", first.name)).font(.caption).foregroundStyle(.secondary)
                            .help(L("레포 이름이 없는 도메인 경로는 기본 레포 것으로 봐요. 순서는 설정 → 레포에서 바꿔요."))
                    }
                    Spacer()
                    Button(m.selectedRepos.count > 1 ? L("고른 레포 %d개 저장", m.selectedRepos.count) : L("이 레포로 저장")) {
                        Task { await m.saveRepo() }
                    }
                    .fixedSize().disabled(m.selectedRepos.isEmpty || m.working != nil)
                }
            } else if let repos = m.config?.repos, !repos.isEmpty {
                ForEach(repos) { r in
                    Label(L("등록된 레포: %@ @ %@", r.url, r.ref), systemImage: "checkmark.circle").font(.callout)
                }
            }
        }
    }

    private var claude: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("Claude API 키")).font(.title3.weight(.semibold))
            Text(L("Claude Console에서 키를 만들고 크레딧을 충전하세요. 질문 하나에 보통 $0.01~$0.10이 들어요. 워크스페이스 사용 한도를 걸어 두길 권해요."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button(L("Claude Console 열기")) { NSWorkspace.shared.open(URL(string: "https://console.anthropic.com/settings/keys")!) }
            TokenField(title: L("API 키"), placeholder: "sk-ant-…", text: $m.apiKey, problem: m.apiKeyProblem,
                       saved: m.secretSaved("ANTHROPIC_API_KEY"))
            Text(L("키는 이 맥의 Keychain에만 저장돼요. 개인 Claude 구독 로그인은 다른 사람에게 답하는 봇에 쓸 수 없어서 지원하지 않아요."))
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var channelsStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("봇이 답할 채널")).font(.title3.weight(.semibold))
            Text(L("봇은 GitHub 토큰이 읽을 수 있는 코드를 근거로 답해요. 고른 채널의 모든 멤버가 그 내용을 볼 수 있으니 범위를 좁게 정하세요. 채널에 봇을 먼저 초대하세요(/invite @AskProject)."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(L("채널 불러오기")) { Task { await m.loadChannels() } }
                Toggle(L("DM으로도 질문 받기"), isOn: $m.allowDM).toggleStyle(.checkbox)
            }
            if !m.channels.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(m.channels) { c in
                            Toggle("#\(c.name)", isOn: Binding(
                                get: { m.allowed.contains(c.id) },
                                set: { on in if on { m.allowed.insert(c.id) } else { m.allowed.remove(c.id) } }))
                                .toggleStyle(.checkbox)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 140)
                Picker(L("운영 채널(호스트 중복 감지·확인 요청)"), selection: $m.hostChannel) {
                    Text(L("없음")).tag("")
                    ForEach(m.channels) { c in Text("#\(c.name)").tag(c.id) }
                }
                .frame(width: 420)
                Text(L("운영 채널에는 이 맥이 켜져 있다는 메시지 하나가 제자리에서 갱신돼요. 다른 맥에서 같은 봇이 켜지면 알려 드려요."))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .task { if m.channels.isEmpty { await m.loadChannels() } }
    }

    private var verify: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("연결 점검")).font(.title3.weight(.semibold))
            Button(L("다시 점검")) { Task { await m.runDoctor() } }.disabled(m.working != nil)
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(m.checks) { c in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: c.symbol).foregroundStyle(c.color)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(c.name).font(.callout.weight(.medium))
                                Text(c.detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer()
                            if c.status != "ok", let step = fixStep(c.step) {
                                Button(L("고치기")) { m.go(step) }.controlSize(.small)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task { await m.runDoctor() }
    }

    private var startStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(m.started ? L("준비 완료") : L("봇 켜기")).font(.title3.weight(.semibold))
            Text(L("레포를 받아 색인한 뒤 봇·웹 콘솔·매일 백업을 켜요. 처음 켤 때 macOS가 '로그인 항목' 허용을 물어보면 허용해 주세요."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if !m.started {
                Button(L("켜기")) { Task { await m.start() } }.buttonStyle(.borderedProminent).disabled(m.working != nil)
            } else {
                Text(L("허용한 채널에서 이렇게 물어보세요: @AskProject 로그인 화면은 어떤 조건에서 보여?")).font(.callout)
                Text(L("팀원도 브라우저로 웹 콘솔(질문·답 기록)을 보게 하려면 다음 단계에서 팀 공유를 설정하세요. 나중에 설정 → 팀 공유에서 해도 돼요."))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(L("맥 운영 설정 열기")) { WindowManager.shared.showSettings(tab: .host) }
                    Button(L("닫기")) { WindowManager.shared.closeWizard() }
                    Button(L("팀 공유 설정하기")) { m.go(.share) }.buttonStyle(.borderedProminent)
                }
            }
        }
    }

    private var shareStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("팀 공유 (선택)")).font(.title3.weight(.semibold))
            Text(L("웹 콘솔을 Cloudflare로 공개 주소에 이어, 허용한 팀원 이메일로만 들어오게 해요. 도메인이 없거나 지금 필요 없으면 건너뛰어도 돼요."))
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            ScrollView {
                TeamShareForm(m: share).padding(.trailing, 12)
            }
            if let n = share.note { NoteView(note: n) }
            TeamShareControls(m: share)
        }
        .task { await share.load() }
    }

    private func fixStep(_ step: String?) -> WizardModel.Step? {
        switch step {
        case "slack": return .slack
        case "github", "repos": return .github
        case "claude": return .claude
        case "channels": return .channels
        default: return nil
        }
    }

    private var footer: some View {
        HStack {
            if let w = m.working {
                ProgressView().controlSize(.small)
                Text(w).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if m.step != .welcome {
                Button(L("이전")) { m.go(WizardModel.Step(rawValue: m.step.rawValue - 1)!) }.disabled(m.working != nil)
            }
            if m.step == .share {
                Button(share.shareOn ? L("완료") : L("건너뛰고 닫기")) { WindowManager.shared.closeWizard() }
            } else if m.step != .start {
                Button(nextLabel) { Task { await next() } }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(m.working != nil || !canAdvance)
            }
        }
    }

    private var nextLabel: String {
        switch m.step {
        case .slack: return m.botToken.isEmpty && m.appToken.isEmpty ? L("다음") : L("저장하고 확인")
        case .claude: return m.apiKey.isEmpty ? L("다음") : L("저장하고 확인")
        default: return L("다음")
        }
    }

    private var canAdvance: Bool {
        switch m.step {
        case .slack:
            return m.botTokenProblem == nil && m.appTokenProblem == nil
                && ((!m.botToken.isEmpty && !m.appToken.isEmpty)
                    || (m.secretSaved("ASKPROJECT_SLACK_BOT_TOKEN") && m.secretSaved("ASKPROJECT_SLACK_APP_TOKEN")))
        case .github: return !(m.config?.repos.isEmpty ?? true)
        case .claude: return m.apiKeyProblem == nil && (!m.apiKey.isEmpty || m.secretSaved("ANTHROPIC_API_KEY"))
        case .channels: return !m.allowed.isEmpty || m.allowDM
        case .verify: return !m.checks.isEmpty && m.blocking.isEmpty
        case .welcome: return privacyAck
        default: return true
        }
    }

    private func next() async {
        switch m.step {
        case .slack:
            if !m.botToken.isEmpty || !m.appToken.isEmpty || m.note?.ok != true {
                await m.saveSlack()
                guard m.note?.ok == true else { return }
            }
        case .claude:
            if !m.apiKey.isEmpty || m.note?.ok != true {
                await m.saveClaude()
                guard m.note?.ok == true else { return }
            }
        case .channels:
            guard await m.saveChannels() else { return }
        default: break
        }
        if let n = WizardModel.Step(rawValue: m.step.rawValue + 1) { m.go(n) }
    }
}

struct TokenField: View {
    let title: String
    let placeholder: String
    @Binding var text: String
    let problem: String?
    let saved: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title).font(.caption).foregroundStyle(.secondary)
                if saved && text.isEmpty {
                    Label(L("저장됨"), systemImage: "checkmark.seal.fill").font(.caption).foregroundStyle(.green)
                }
            }
            SecureField(saved ? L("저장된 값 유지 (바꾸려면 새로 붙여넣기)") : placeholder, text: $text)
                .textFieldStyle(.roundedBorder)
            if let problem {
                Text(problem).font(.caption).foregroundStyle(.red)
            }
        }
    }
}

struct NoteView: View {
    let note: CheckResult

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: note.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(note.ok ? .green : .orange)
            Text(note.detail).font(.callout).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill((note.ok ? Color.green : Color.orange).opacity(0.1)))
    }
}
