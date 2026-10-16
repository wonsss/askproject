// 설정 창: 연결(비밀값) · 레포 · 채널 · 팀 공유(웹 콘솔 Cloudflare 터널) · 맥 운영(백업 사본·복구·절전·자동 로그인) · 도메인·용어(보기만) · 정보.
// 저장은 모두 `askproject config set --json`(검증·원자적 쓰기, 비밀값은 Keychain).

import AppKit
import SwiftUI

enum SettingsTab: Hashable { case connections, repos, channels, share, host, domains, about }

@MainActor
final class SettingsModel: ObservableObject {
    @Published var config: ConfigState?
    @Published var note: CheckResult?
    @Published var working = false
    @Published var secrets: [String: String] = [:]
    @Published var repos: [RepoEntry] = []
    @Published var channels: [SlackChannel] = []
    @Published var channelsError: String?
    @Published var loadingChannels = false
    @Published var allowed: Set<String> = []
    @Published var allowDM = false
    @Published var hostChannel = ""
    @Published var escalation = ""  // 비면 운영 채널을 쓴다
    @Published var shareHost = ""
    @Published var shareTeam = ""
    @Published var shareAud = ""
    @Published var shareToken = ""
    @Published var shareOn = false
    @Published var shareCheck: CheckResult?
    @Published var checkingShare = false
    @Published var detectingTeam = false
    @Published var teamDetect: CheckResult?
    @Published var notionRoot = ""
    @Published var checks: [String: CheckResult] = [:]
    @Published var checking: String?

    func load() async {
        let (c, out) = await Backend.json(ConfigState.self, ["config", "get", "--json"])
        config = c
        if c == nil { note = CheckResult(ok: false, detail: "설정을 읽지 못했어요: " + out.errorLine, step: nil) }
        repos = c?.repos ?? []
        notionRoot = c?.settings.notion_root_page_id ?? ""
        allowedText = c?.settings.allowed_channels ?? ""
        hostChannel = c?.settings.host_channel ?? ""
        escalation = c?.settings.escalation_channel ?? ""
        if escalation == hostChannel { escalation = "" }
        shareHost = c?.settings.console_public_host ?? ""
        shareTeam = c?.settings.cf_access_team_domain ?? ""
        shareAud = c?.settings.cf_access_aud ?? ""
        shareOn = Backend.isBundled && Agents.tunnelEnabled
    }

    /// 대시보드의 설치 명령 전체(`cloudflared service install eyJ…`)를 붙여넣어도 토큰만 쓴다.
    static func tunnelToken(_ pasted: String) -> String {
        let words = pasted.split(whereSeparator: \.isWhitespace).map(String.init)
        return words.last(where: { $0.hasPrefix("eyJ") }) ?? pasted.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 이 터널의 대시보드 Routes 화면. 방금 붙여넣은(저장 전) 토큰이 있으면 그걸, 없으면 저장된 토큰(config get)을 쓴다.
    var routesURL: URL? {
        currentTunnelIDs.map { CloudflareDash.url(CloudflareDash.routes(tunnel: $0.tunnel), account: $0.account) }
    }

    /// Access 앱 목록. 계정 ID를 알면 그 계정 주소로 바로 연다.
    var accessAppsURL: URL { CloudflareDash.url(CloudflareDash.accessApps, account: currentTunnelIDs?.account) }

    private var currentTunnelIDs: (account: String, tunnel: String)? {
        if let ids = Self.tunnelIDs(Self.tunnelToken(shareToken)) { return ids }
        return config?.tunnel.map { ($0.account_id, $0.tunnel_id) }
    }

    /// 토큰(base64 JSON {a, t, s})의 계정·터널 ID. 주소에 넣으므로 형식이 맞을 때만 돌려준다(백엔드 tunnel.dashboard_info와 같은 규칙).
    static func tunnelIDs(_ token: String) -> (account: String, tunnel: String)? {
        guard token.hasPrefix("eyJ") else { return nil }
        var b64 = token
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let a = obj["a"] as? String, let t = obj["t"] as? String,
              a.count == 32, a.allSatisfy(\.isHexDigit), UUID(uuidString: t) != nil else { return nil }
        return (a.lowercased(), t.lowercased())
    }

    var shareTokenSaved: Bool { config?.secrets["ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN"] ?? false }

    var shareComplete: Bool {
        !shareHost.isEmpty && !shareTeam.isEmpty && !shareAud.isEmpty && (shareTokenSaved || !shareToken.isEmpty)
    }

    /// 'https://askproject.example.com/'처럼 붙여넣어도 호스트 이름만 쓴다.
    static func host(_ s: String) -> String {
        var v = s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for p in ["https://", "http://"] where v.hasPrefix(p) { v.removeFirst(p.count) }
        return v.split(separator: "/").first.map(String.init) ?? v
    }

    /// 팀 이름만 넣으면 .cloudflareaccess.com을 붙인다.
    static func teamDomain(_ s: String) -> String {
        let v = host(s)
        return v.isEmpty || v.contains(".") ? v : v + ".cloudflareaccess.com"
    }

    var shareHostProblem: String? {
        let v = Self.host(shareHost)
        return v.isEmpty || v.contains(".") ? nil : "askproject.example.com처럼 도메인까지 넣어 주세요"
    }

    var shareTeamProblem: String? {
        let v = Self.teamDomain(shareTeam)
        return v.isEmpty || v.hasSuffix(".cloudflareaccess.com") ? nil : "팀 도메인은 <팀 이름>.cloudflareaccess.com 형식이에요"
    }

    var shareAudProblem: String? {
        let v = shareAud.trimmingCharacters(in: .whitespacesAndNewlines)
        let hex = v.count == 64 && v.allSatisfy(\.isHexDigit)
        return v.isEmpty || hex ? nil : "AUD 태그는 64자리 16진수예요 (지금 \(v.count)자리)"
    }

    var shareHasProblem: Bool { shareHostProblem != nil || shareTeamProblem != nil || shareAudProblem != nil }

    /// 저장 → 콘솔 다시 시작(공개 호스트·Access 값은 시작할 때 읽는다) → 켜져 있으면 터널도 새 토큰으로.
    func saveShare() async {
        var secrets: [String: String] = [:]
        let token = Self.tunnelToken(shareToken)
        if !token.isEmpty { secrets["ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN"] = token }
        working = true
        let r = await Backend.setConfig(settings: ["console_public_host": Self.host(shareHost),
                                                   "cf_access_team_domain": Self.teamDomain(shareTeam),
                                                   "cf_access_aud": shareAud.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()],
                                        secrets: secrets)
        working = false
        note = CheckResult(ok: r.ok, detail: r.ok ? "저장했어요. 웹 콘솔을 다시 시작해요." : (r.error ?? "저장 실패"), step: nil)
        guard r.ok else { return }
        shareToken = ""
        // 콘솔은 공개 호스트·Access 값을 시작할 때 읽는다. 배포 앱(SMAppService)과 개발 호스트 모드(host on)의
        // launchd 레이블이 같아서 kickstart는 두 모드 모두에서 동작한다(등록돼 있지 않으면 아무 일도 없다)
        Agents.restart("console")
        if Backend.isBundled && Agents.tunnelEnabled { Agents.restart(Agents.tunnel) }
        await load()
    }

    /// 2번 단계: 토큰을 저장하고 바로 터널을 켠다(대시보드의 Continue가 연결을 기다리므로).
    func saveTokenAndStart() async {
        await saveShare()
        guard note?.ok == true, shareTokenSaved, Backend.isBundled else { return }
        setShare(true)
    }

    func setShare(_ on: Bool) {
        do {
            let approval = try Agents.setTunnel(on)
            note = CheckResult(ok: !approval, detail: approval
                               ? "시스템 설정 → 로그인 항목에서 AskProject를 허용하면 터널이 켜져요."
                               : (on ? "팀 공유를 켰어요. 30초쯤 뒤 '연결 확인'을 눌러 보세요." : "팀 공유를 껐어요. 이제 이 맥에서만 열려요."),
                               step: nil)
        } catch {
            note = CheckResult(ok: false, detail: "팀 공유를 바꾸지 못했어요: \(error.localizedDescription)", step: nil)
        }
        shareOn = Agents.tunnelEnabled
        shareCheck = nil
    }

    /// 연결 탭의 서비스별 확인(`setup check slack|github|claude`). 저장된 값으로 확인한다.
    func check(_ target: String) async {
        checking = target
        let (r, out) = await Backend.json(CheckResult.self, ["setup", "check", target])
        checking = nil
        checks[target] = r ?? CheckResult(ok: false, detail: out.errorLine, step: target)
    }

    var notionRootChanged: Bool {
        notionRoot.trimmingCharacters(in: .whitespaces) != (config?.settings.notion_root_page_id ?? "")
    }

    /// 공개 주소의 Access 로그인 리다이렉트에서 팀 도메인을 읽어 칸을 채운다(`setup team-domain`).
    func detectTeam() async {
        detectingTeam = true
        let (r, out) = await Backend.json(TeamDomainResult.self, ["setup", "team-domain", "--host", Self.host(shareHost)])
        detectingTeam = false
        if let team = r?.team_domain { shareTeam = team }
        teamDetect = CheckResult(ok: r?.ok ?? false, detail: r?.detail ?? out.errorLine, step: "tunnel")
    }

    func checkShare() async {
        checkingShare = true
        let (r, out) = await Backend.json(CheckResult.self, ["setup", "check", "tunnel"])
        checkingShare = false
        shareCheck = r ?? CheckResult(ok: false, detail: out.errorLine, step: "tunnel")
    }

    /// 쉼표 구분 문자열 ↔ 체크 상태. 'dm'은 DM 허용 표시.
    var allowedText: String {
        get { (Array(allowed).sorted() + (allowDM ? ["dm"] : [])).joined(separator: ",") }
        set {
            let ids = newValue.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            allowed = Set(ids.filter { $0 != "dm" })
            allowDM = ids.contains("dm")
        }
    }

    /// 불러온 채널 + 저장돼 있지만 목록에 없는 채널(봇이 나갔거나 보관된 채널)을 함께 보여 준다.
    var channelChoices: [SlackChannel] {
        let known = Set(channels.map(\.id))
        let saved = Set(allowed).union([hostChannel, escalation]).filter { !$0.isEmpty && !known.contains($0) }
        return channels + saved.sorted().map { SlackChannel(id: $0, name: "\($0) (목록에 없음)") }
    }

    func label(_ c: SlackChannel) -> String { channels.contains(c) ? "#\(c.name)" : c.name }

    func loadChannels() async {
        loadingChannels = true
        let (list, out) = await Backend.json(ChannelList.self, ["setup", "channels"])
        loadingChannels = false
        channels = list?.channels ?? []
        channelsError = list.map { $0.ok ? nil : $0.detail } ?? out.errorLine
    }

    func save(settings: [String: Any] = [:], secrets: [String: String] = [:], repos: [[String: String]]? = nil,
              restartBot: Bool = true, saved: String? = nil) async {
        working = true
        let r = await Backend.setConfig(settings: settings, secrets: secrets, repos: repos)
        working = false
        let ok = saved ?? (restartBot ? "저장했어요. 실행 중인 봇에 적용하려고 다시 시작해요." : "저장했어요.")
        note = CheckResult(ok: r.ok, detail: r.ok ? ok : (r.error ?? "저장 실패"), step: nil)
        if r.ok {
            self.secrets = [:]
            if restartBot { AppModel.shared.restartBotIfRunning() }
            await load()
        }
    }

    /// 백업 사본 폴더(동기화 폴더) 고르기. 경로 검증·저장은 `config set`이 한다. 백업은 봇과 따로 돌아 재시작이 필요 없다.
    func chooseBackupCopyDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "이 폴더에 사본 두기"
        panel.message = "iCloud Drive나 Google Drive처럼 맥 밖으로 동기화되는 폴더를 고르세요. 백업할 때마다 사본을 여기에 둬요."
        if let current = config?.settings.backup_copy_dir, !current.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: current)
        } else {
            let icloud = NSString(string: "~/Library/Mobile Documents/com~apple~CloudDocs").expandingTildeInPath
            if FileManager.default.fileExists(atPath: icloud) { panel.directoryURL = URL(fileURLWithPath: icloud) }
        }
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task {
            await save(settings: ["backup_copy_dir": url.path], restartBot: false,
                       saved: "저장했어요. 다음 백업부터 이 폴더에도 사본을 둬요. 지금 백업하면 바로 첫 사본이 생겨요.")
        }
    }
}

struct SettingsView: View {
    @StateObject private var m = SettingsModel()
    @ObservedObject var app = AppModel.shared
    @State var tab: SettingsTab

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TabView(selection: $tab) {
                connections.tabItem { Text("연결") }.tag(SettingsTab.connections)
                reposTab.tabItem { Text("레포") }.tag(SettingsTab.repos)
                channels.tabItem { Text("채널") }.tag(SettingsTab.channels)
                share.tabItem { Text("팀 공유") }.tag(SettingsTab.share)
                host.tabItem { Text("맥 운영") }.tag(SettingsTab.host)
                domains.tabItem { Text("도메인·용어") }.tag(SettingsTab.domains)
                about.tabItem { Text("정보") }.tag(SettingsTab.about)
            }
            .padding(16)
            if let n = m.note { NoteView(note: n).padding([.horizontal, .bottom], 16) }
        }
        .frame(width: 640, height: 560)
        .task { await m.load() }
    }

    private var connections: some View {
        SettingsPage {
            Text("값은 이 맥의 Keychain에 저장돼요. 바꿀 항목만 새로 붙여넣고 저장하세요. 비워 둔 칸은 저장된 값을 그대로 써요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

            connectionGroup("슬랙", "슬랙 앱을 다시 설치했거나 토큰을 재발급했다면 여기에 새로 넣어 주세요.", check: "slack") {
                Button("슬랙 앱 관리 열기") { NSWorkspace.shared.open(URL(string: "https://api.slack.com/apps")!) }
            } fields: {
                secretField("ASKPROJECT_SLACK_BOT_TOKEN", "봇 토큰 — 앱 고르기 → Install App → Bot User OAuth Token", prefix: "xoxb-")
                secretField("ASKPROJECT_SLACK_APP_TOKEN",
                            "앱 토큰 — Basic Information → App-Level Tokens → Generate (connections:write)", prefix: "xapp-")
            }

            connectionGroup("GitHub", "레포 내용 읽기(contents: read)만 있는 토큰이에요. 90일 뒤 만료되니 새로 만들어 붙여넣으세요. 'Repository access'에서 봇이 읽을 레포를 고르면 돼요.",
                            check: "github") {
                Button("새 토큰 만들기 열기") {
                    Task {
                        let (u, _) = await Backend.json(URLResult.self, ["setup", "pat-url"])
                        if let u, let url = URL(string: u.url) { NSWorkspace.shared.open(url) }
                    }
                }
                Button("내 토큰 목록 열기") {
                    NSWorkspace.shared.open(URL(string: "https://github.com/settings/personal-access-tokens")!)
                }
            } fields: {
                secretField("ASKPROJECT_GITHUB_TOKEN", "토큰", prefix: "github_pat_")
                if let t = app.status?.github_token, let d = t.days_left {
                    Text("만료까지 \(Int(d.rounded()))일").font(.caption).foregroundStyle(t.warning ? .orange : .secondary)
                }
            }

            connectionGroup("Claude", "Claude Console에서 키를 만들고 크레딧을 충전하세요. 워크스페이스 사용 한도를 걸어 두길 권해요. 확인에는 약 $0.00001이 들어요.",
                            check: "claude") {
                Button("API 키 열기") { NSWorkspace.shared.open(URL(string: "https://console.anthropic.com/settings/keys")!) }
                Button("결제·한도 열기") { NSWorkspace.shared.open(URL(string: "https://console.anthropic.com/settings/billing")!) }
            } fields: {
                secretField("ANTHROPIC_API_KEY", "API 키", prefix: "sk-ant-")
            }

            connectionGroup("노션 (선택)", nil, check: nil) {
                Button("노션 연결 관리 열기") {
                    NSWorkspace.shared.open(URL(string: "https://app.notion.com/developers/connections")!)
                }
            } fields: {
                VStack(alignment: .leading, spacing: 4) {
                    shareStep(1, "노션 설정 → 연결 → 개발자 도구 → 신규 연결. 이름은 AskProject, 인증 방법은 API 토큰이에요.")
                    shareStep(2, "구성 탭에서 기능은 '콘텐츠 읽기'만 켜고, 토큰(ntn_…)을 아래에 붙여넣으세요.")
                    shareStep(3, "기획 문서 최상위 페이지 → ⋯ → 연결 → 연결 추가 → AskProject. 하위 페이지도 함께 읽어요.")
                    shareStep(4, "그 페이지 주소 끝 32자리를 최상위 페이지 ID에 넣으세요. 다음 동기화부터 반영돼요.")
                }
                secretField("ASKPROJECT_NOTION_TOKEN", "API 토큰", prefix: "", placeholder: "ntn_…")
                LabeledField("최상위 페이지 ID", placeholder: "주소 끝 32자리", text: $m.notionRoot)
            }
        } footer: {
            Spacer()
            Button("저장") {
                let filled = m.secrets.filter { !$0.value.isEmpty }.mapValues { $0.trimmingCharacters(in: .whitespaces) }
                let settings: [String: Any] = m.notionRootChanged
                    ? ["notion_root_page_id": m.notionRoot.trimmingCharacters(in: .whitespaces)] : [:]
                Task { await m.save(settings: settings, secrets: filled) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(m.working || (m.secrets.values.allSatisfy(\.isEmpty) && !m.notionRootChanged))
        }
    }

    /// 서비스 하나: 제목·설명 → 바로 가기 링크 → 입력 칸 → (있으면) 저장된 값으로 연결 확인.
    private func connectionGroup<Links: View, Fields: View>(
        _ title: String, _ subtitle: String?, check: String?,
        @ViewBuilder links: () -> Links, @ViewBuilder fields: () -> Fields
    ) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                SectionHeader(title, subtitle)
                HStack {
                    links()
                    Spacer()
                    if let check {
                        Button(m.checking == check ? "확인 중…" : "연결 확인") { Task { await m.check(check) } }
                            .disabled(m.checking != nil)
                            .help("저장된 값으로 확인해요. 새로 붙여넣은 값은 저장한 뒤 확인하세요.")
                    }
                }
                .controlSize(.small)
                fields()
                if let check, let r = m.checks[check] { NoteView(note: r) }
            }
            .padding(6)
        }
    }

    private func secretField(_ key: String, _ title: String, prefix: String, placeholder: String? = nil) -> some View {
        let binding = Binding(get: { m.secrets[key] ?? "" }, set: { m.secrets[key] = $0 })
        let problem: String? = {
            if prefix.isEmpty { return nil }
            if prefix == "github_pat_", binding.wrappedValue.hasPrefix("ghp_") { return nil }  // classic도 받는다
            if prefix == "xapp-", binding.wrappedValue.hasPrefix("xoxb-") { return "봇 토큰이 아니라 앱 수준 토큰(xapp-)을 넣어 주세요" }
            return WizardModel.prefixProblem(binding.wrappedValue, prefix, title.components(separatedBy: " — ")[0])
        }()
        return TokenField(title: title, placeholder: placeholder ?? (prefix.isEmpty ? "" : prefix + "…"), text: binding,
                          problem: problem, saved: m.config?.secrets[key] ?? false)
    }

    private var reposTab: some View {
        SettingsPage {
            SectionHeader("읽을 레포", "현재는 첫 번째 레포만 답에 쓰여요. 바꾸면 다음 동기화 때 새로 받아요.")
            if m.repos.isEmpty {
                Text("등록된 레포가 없어요.").foregroundStyle(.secondary)
            }
            ForEach($m.repos) { $r in
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .top, spacing: 10) {
                            LabeledField("이름", placeholder: "repo1", text: $r.name).frame(width: 140)
                            LabeledField("브랜치", placeholder: "main", text: $r.ref).frame(width: 120)
                            Spacer()
                            Button(role: .destructive) { m.repos.removeAll { $0.id == r.id } } label: {
                                Label("삭제", systemImage: "trash")
                            }
                            .buttonStyle(.borderless).foregroundStyle(.red).padding(.top, 18)
                        }
                        LabeledField("주소", placeholder: "https://github.com/조직/레포", text: $r.url)
                        LabeledField("한 줄 설명", placeholder: "무슨 서비스인지 (프롬프트에 들어가요)", text: $r.description)
                    }
                    .padding(6)
                }
            }
        } footer: {
            Button {
                m.repos.append(RepoEntry(name: "repo\(m.repos.count + 1)", url: "", ref: "main", description: ""))
            } label: { Label("레포 추가", systemImage: "plus") }
            Spacer()
            Button("저장") {
                let rows = m.repos.map { ["name": $0.name, "url": $0.url, "ref": $0.ref, "description": $0.description] }
                Task { await m.save(repos: rows) }
            }
            .buttonStyle(.borderedProminent).disabled(m.working || m.repos.isEmpty)
        }
    }

    private var channels: some View {
        SettingsPage {
            SectionHeader("답할 채널", "봇이 질문에 답할 채널이에요. 고른 채널의 모든 멤버가 GitHub 토큰이 읽을 수 있는 코드 내용을 보게 되니 좁게 고르세요.")
            if m.channels.isEmpty && !m.loadingChannels {
                if let e = m.channelsError { NoteView(note: CheckResult(ok: false, detail: "채널 목록을 못 불러왔어요: " + e, step: nil)) }
                LabeledField("채널 ID 직접 입력", placeholder: "C0123ABCD, C0456EFGH, dm", text: $m.allowedText,
                             caption: "쉼표로 구분하고, DM을 허용하려면 dm을 넣어요. 채널 ID는 슬랙에서 채널 이름 → 채널 세부정보 맨 아래에 있어요.")
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(m.channelChoices) { c in
                        Toggle(m.label(c), isOn: Binding(
                            get: { m.allowed.contains(c.id) },
                            set: { on in if on { m.allowed.insert(c.id) } else { m.allowed.remove(c.id) } }))
                            .toggleStyle(.checkbox)
                    }
                    Divider().padding(.vertical, 2)
                    Toggle("DM으로도 질문 받기", isOn: $m.allowDM).toggleStyle(.checkbox)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))
                Text(m.allowed.isEmpty && !m.allowDM
                     ? "아무것도 고르지 않으면 봇이 초대된 모든 채널에서 답해요."
                     : "목록에는 봇이 들어간 채널만 보여요. 없는 채널은 슬랙에서 /invite @AskProject 후 다시 불러오세요.")
                    .font(.caption).foregroundStyle(m.allowed.isEmpty && !m.allowDM ? .orange : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            SectionHeader("운영 채널", "개발팀이 보는 채널 하나를 고르세요. 이 맥이 켜져 있다는 메시지 하나가 제자리에서 갱신되고, 다른 맥에서 같은 봇이 켜지면 알려 줘요.")
            Picker("운영 채널", selection: $m.hostChannel) {
                Text("없음").tag("")
                ForEach(m.channelChoices) { c in Text(m.label(c)).tag(c.id) }
            }
            .labelsHidden().frame(maxWidth: 360, alignment: .leading)

            SectionHeader("확인 요청 채널", "답변의 '개발자에게 확인 요청' 버튼이 이 채널로 보내요.")
            Picker("확인 요청 채널", selection: $m.escalation) {
                Text(m.hostChannel.isEmpty ? "운영 채널과 같게 (지금은 없음)" : "운영 채널과 같게").tag("")
                ForEach(m.channelChoices) { c in Text(m.label(c)).tag(c.id) }
            }
            .labelsHidden().frame(maxWidth: 360, alignment: .leading)
        } footer: {
            Button(m.loadingChannels ? "불러오는 중…" : "채널 다시 불러오기") { Task { await m.loadChannels() } }
                .disabled(m.loadingChannels)
            Spacer()
            Button("저장") {
                Task { await m.save(settings: ["allowed_channels": m.allowedText, "host_channel": m.hostChannel,
                                               "escalation_channel": m.escalation]) }
            }
            .buttonStyle(.borderedProminent).disabled(m.working)
        }
        .task { if m.channels.isEmpty { await m.loadChannels() } }
    }

    private var share: some View {
        SettingsPage {
            SectionHeader("웹 콘솔을 팀원과 함께 보기",
                          "팀원은 브라우저에서 https://<공개 주소>로 들어오고, 허용 목록에 있는 이메일로 받은 코드로 로그인해요. 콘솔은 계속 이 맥(127.0.0.1)에만 열려 있고, Cloudflare Tunnel이 바깥과 이어 줘요. 아래 순서대로 하면 10분쯤 걸려요.")
            if !app.isApp {
                Text("개발 앱에서는 켜고 끌 수 없어요. docs/operations.md의 CLI 방식(cloudflared + launchd)을 쓰세요.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            TeamShareForm(m: m)
        } footer: {
            TeamShareControls(m: m, isApp: app.isApp)
        }
    }

    private func shareStep(_ n: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("\(n).").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            Text(text).font(.caption).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var host: some View {
        let power = app.status?.power
        return SettingsPage {
            backupCopy
            Divider().padding(.vertical, 4)
            SectionHeader("절전", "맥이 잠들면 봇도 멈춰요.")
            Picker("절전", selection: Binding(get: { power?.mode ?? "lid-open" }, set: { app.setPower($0) })) {
                Text("기본 — 맥이 잠들면 봇도 멈춤").tag("default")
                Text("덮개 열림 — 깨어 있음(화면은 꺼져도 됨)").tag("lid-open")
                Text("덮개 닫힘 — 전원 연결 중이면 닫아도 깨어 있음").tag("lid-closed")
            }
            .pickerStyle(.radioGroup).labelsHidden()
            Text("덮개 닫힘 모드는 전원이 연결돼 있을 때만 동작해요. 가방에 넣으면 발열에 주의하세요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider().padding(.vertical, 4)
            SectionHeader("자동 로그인", nil)
            HStack {
                Text("현재")
                Text(power?.autologin_user.map { "켜짐 (\($0))" } ?? "꺼짐")
                    .foregroundStyle(power?.autologin_user == nil ? .orange : .primary)
            }
            if power?.filevault_on == true {
                Text("FileVault가 켜져 있으면 자동 로그인을 쓸 수 없어요. 재부팅 뒤 누군가 로그인할 때까지 봇이 멈춰요. 상시 운영 맥이라면 보안 정책과 함께 판단하세요.")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("재부팅(정전·업데이트) 뒤에도 봇이 다시 뜨려면 자동 로그인이 필요해요.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("사용자 및 그룹 설정 열기") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Users-Groups-Settings.extension")!)
                }
                Button("로그인 항목 열기") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension")!)
                }
            }
        }
    }

    /// 맥 밖 보관: 동기화 폴더에 백업 사본을 둔다. 백업 폴더는 이 맥 안에 있어서 맥을 잃으면 함께 사라진다.
    private var backupCopy: some View {
        let dir = m.config?.settings.backup_copy_dir ?? ""
        return VStack(alignment: .leading, spacing: 8) {
            SectionHeader("백업 사본 폴더",
                          "백업은 이 맥 안에만 있어서, 맥을 잃거나 디스크가 고장 나면 질문·답 기록도 함께 사라져요. iCloud Drive나 Google Drive 폴더를 고르면 백업할 때마다(매일 03:30, '지금 백업') 그 폴더에도 사본을 둬요.")
            HStack(spacing: 6) {
                Image(systemName: dir.isEmpty ? "exclamationmark.triangle.fill" : "icloud")
                    .foregroundStyle(dir.isEmpty ? .orange : .secondary)
                Text(dir.isEmpty ? "정하지 않음 — 이 맥에만 백업이 있어요" : dir)
                    .font(.callout).lineLimit(2).truncationMode(.middle).textSelection(.enabled)
            }
            if let b = app.status?.last_backup, !dir.isEmpty {
                Text(b.offsite ? "최근 백업(\(b.name))의 사본이 이 폴더에 있어요."
                               : "최근 백업의 사본이 아직 이 폴더에 없어요. '지금 백업'을 누르거나 다음 백업을 기다리세요.")
                    .font(.caption).foregroundStyle(b.offsite ? Color.secondary : Color.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button(dir.isEmpty ? "폴더 고르기…" : "다른 폴더 고르기…") { m.chooseBackupCopyDir() }.disabled(m.working)
                if !dir.isEmpty {
                    Button("Finder에서 보기") { NSWorkspace.shared.open(URL(fileURLWithPath: dir)) }
                    Button("사본 두지 않기") {
                        Task { await m.save(settings: ["backup_copy_dir": ""], restartBot: false) }
                    }
                    .disabled(m.working)
                }
                Spacer()
                Button("지금 백업") { app.backupNow() }.disabled(app.busy != nil)
            }
            Text("오래된 사본은 90일이 지나면 그 폴더에서 정리해요(askproject-로 시작하는 백업 파일만). 사본에는 질문·답 원문이 들어 있으니 팀 공유 폴더가 아닌 개인 폴더를 고르세요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var domains: some View {
        SettingsPage {
            Text("도메인 맵과 용어 사전은 이번 버전에서 화면 편집을 지원하지 않아요. 기본값으로도 동작하고, 채우면 답의 정확도가 올라가요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let dir = app.paths?.config_dir {
                ForEach(["domains.yaml", "glossary.yaml"], id: \.self) { f in
                    GroupBox(f) {
                        ScrollView {
                            Text((try? String(contentsOfFile: dir + "/" + f, encoding: .utf8)) ?? "(없음)")
                                .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 140)
                    }
                }
                Button("Finder에서 보기") { NSWorkspace.shared.open(URL(fileURLWithPath: dir)) }
            }
        }
    }

    private var about: some View {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "-"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "-"
        return SettingsPage {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                infoRow("앱", "\(version) (\(build))")
                infoRow("백엔드", "\(app.status?.version ?? "-") · 계약 v\(app.status?.protocol ?? 0)")
                infoRow("모드", app.isApp ? "배포 앱 (Keychain)" : "개발 (레포 .env)")
                if let p = app.paths {
                    infoRow("데이터", p.data_dir)
                    infoRow("로그", p.log_dir)
                }
            }
            HStack {
                Button("진단 묶음 만들기") { app.diagnostics() }
                if Updater.shared.available { Button("업데이트 확인") { Updater.shared.checkForUpdates() } }
                Button("제3자 고지") {
                    if let u = Bundle.main.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "txt") { NSWorkspace.shared.open(u) }
                }
            }
            .padding(.top, 6)
        }
    }

    private func infoRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// 설정 탭 공통 틀: 위에서부터 쌓고, 넘치면 스크롤, 저장 같은 동작은 아래 고정 줄에 둔다.
struct SettingsPage<Content: View, Footer: View>: View {
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
            }
            if Footer.self != EmptyView.self {
                Divider()
                HStack { footer }.padding(.horizontal, 20).padding(.vertical, 12)
            }
        }
    }
}

extension SettingsPage where Footer == EmptyView {
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
        self.footer = EmptyView()
    }
}

struct SectionHeader: View {
    let title: String
    let subtitle: String?

    init(_ title: String, _ subtitle: String?) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.headline)
            if let subtitle {
                Text(subtitle).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// 라벨을 필드 위에 두는 입력 칸(Form의 왼쪽 라벨 열을 쓰지 않는다).
struct LabeledField: View {
    let title: String
    let placeholder: String
    @Binding var text: String
    var caption: String? = nil

    init(_ title: String, placeholder: String, text: Binding<String>, caption: String? = nil) {
        self.title = title
        self.placeholder = placeholder
        self._text = text
        self.caption = caption
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField(title, text: $text, prompt: Text(placeholder)).labelsHidden().textFieldStyle(.roundedBorder)
            if let caption {
                Text(caption).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
