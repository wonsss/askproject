// 복구 화면: 백업 목록 → 고른 백업 확인(restore-check) → 확인 대화상자 → 봇·콘솔 멈춤 → `askproject restore --yes`
// → 다시 켬 → doctor 결과. 파일 교체는 백엔드가 한다(지금 DB는 지우지 않고 before-restore-<시각>으로 옮겨 둔다).
// 배포 앱은 SMAppService 작업을 bootout → 재등록, 개발 앱은 `askproject host off` → `host on`.

import AppKit
import SwiftUI

@MainActor
final class RestoreModel: ObservableObject {
    enum Phase: Equatable { case choose, running, done }

    @Published var list: BackupList?
    @Published var listError: String?
    @Published var picked: BackupItem?
    @Published var extra: BackupItem?  // '다른 파일 고르기'로 고른 파일(새 맥에서 사본 폴더의 백업 등)
    @Published var check: BackupCheck?
    @Published var checking = false
    @Published var confirm = false
    @Published var phase: Phase = .choose
    @Published var step = ""
    @Published var result: RestoreResult?
    @Published var doctor: [DoctorItem] = []

    var items: [BackupItem] { (extra.map { [$0] } ?? []) + (list?.backups ?? []) }

    func load() async {
        let (l, out) = await Backend.json(BackupList.self, ["backups", "--json"])
        list = l
        listError = l == nil ? out.errorLine : nil
    }

    func select(_ b: BackupItem) {
        picked = b
        check = nil
        Task { await verify(b) }
    }

    func verify(_ b: BackupItem) async {
        checking = true
        let (c, out) = await Backend.json(BackupCheck.self, ["restore-check", "--json", b.path])
        checking = false
        guard picked == b else { return }
        check = c ?? BackupCheck(ok: false, path: b.path, sha256_ok: nil, problems: nil, integrity: nil, schema_version: nil,
                                 queries: nil, results: nil, latest: nil, error: out.errorLine)
    }

    func chooseFile() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "복구할 백업 파일(askproject-….sqlite3)을 고르세요. 같은 폴더에 .sha256가 있으면 함께 확인해요."
        panel.prompt = "이 파일 확인"
        if let d = list?.copy_dir { panel.directoryURL = URL(fileURLWithPath: d) }
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        let b = BackupItem(name: url.lastPathComponent, path: url.path, kind: "file",
                           bytes: (attrs?[.size] as? Int) ?? 0,
                           mtime: (attrs?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0,
                           sha256_ok: nil, queries: nil, schema_version: nil, latest_ts: nil, error: nil)
        extra = b
        select(b)
    }

    /// 봇·콘솔·매일 백업을 멈추고 복구한 뒤, 켜져 있던 것만 다시 켠다. 복구가 실패해도(백엔드가 되돌림) 다시 켠다.
    func run(_ b: BackupItem) async {
        phase = .running
        let app = AppModel.shared
        var paused: [String] = []
        var devWasOn = false
        step = "봇·웹 콘솔을 멈추는 중"
        if app.isApp {
            paused = Agents.pauseEnabled(Agents.names)
        } else {
            devWasOn = app.status?.jobs.values.contains { $0.state != "off" } ?? false
            if devWasOn { _ = await Backend.run(["host", "off"]) }
        }

        step = "백업으로 바꾸는 중"
        var r: RestoreResult?
        for attempt in 0..<8 {  // 멈춘 프로세스가 잠금을 놓을 때까지 잠깐 기다린다
            let (res, out) = await Backend.json(RestoreResult.self, ["restore", b.path, "--yes", "--json"])
            r = res ?? RestoreResult(ok: false, error: out.errorLine, restored_from: nil, moved_aside: nil,
                                     pre_restore_backup: nil, pre_restore_error: nil, queries: nil, latest: nil)
            let busy = out.code == 3 && (r?.error?.contains("실행 중") ?? false)
            if !busy || attempt == 7 { break }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        result = r

        step = "봇·웹 콘솔을 다시 켜는 중"
        if app.isApp {
            if !paused.isEmpty {
                _ = await Backend.run(["migrate"])  // 예전 스키마의 백업이면 먼저 올린다(앞서 백업한다)
                Agents.resume(paused)
            }
        } else if devWasOn {
            _ = await Backend.run(["host", "on"])
        }

        step = "점검하는 중"
        let (d, _) = await Backend.json([DoctorItem].self, ["doctor", "--json"])
        doctor = d ?? []
        phase = .done
        app.refresh()
        await load()
    }
}

struct RestoreView: View {
    @StateObject private var m = RestoreModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch m.phase {
            case .choose: chooser
            case .running:
                Spacer()
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(m.step)
                }
                .frame(maxWidth: .infinity)
                Text("창을 닫지 마세요. 1분 안에 끝나요.").font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity)
                Spacer()
            case .done: done
            }
        }
        .padding(20)
        .frame(width: 600, height: 520)
        .task { await m.load() }
    }

    private var chooser: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader("백업에서 복구",
                          "질문·답 기록(감사 로그)을 고른 백업 시점으로 되돌려요. 지금 DB는 지우지 않고 옆에 옮겨 두고, 복구 직전 상태도 한 번 더 백업해요. 백업 이후에 들어온 질문은 화면에서 사라져요.")
            if let e = m.listError { Text("목록을 읽지 못했어요: \(e)").font(.caption).foregroundStyle(.red) }
            List(m.items, selection: Binding(get: { m.picked }, set: { if let b = $0 { m.select(b) } })) { b in
                row(b).tag(b)
            }
            .frame(minHeight: 200)
            .overlay {
                if m.list != nil && m.items.isEmpty {
                    Text("백업이 없어요. 다른 맥의 사본이 있으면 '다른 파일 고르기'로 고르세요.").font(.callout).foregroundStyle(.secondary)
                }
            }
            checkBox
            HStack {
                Button("다른 파일 고르기…") { m.chooseFile() }
                Spacer()
                Button("이 백업으로 복구…") { m.confirm = true }
                    .buttonStyle(.borderedProminent)
                    .disabled(m.check?.ok != true || m.checking)
            }
        }
        .alert("이 백업으로 복구할까요?", isPresented: $m.confirm, presenting: m.picked) { b in
            Button("복구", role: .destructive) { Task { await m.run(b) } }
            Button("취소", role: .cancel) {}
        } message: { b in
            Text("""
            \(b.name) (\(date(b.mtime)))
            봇·웹 콘솔을 잠깐 멈추고 DB를 이 백업으로 바꾼 뒤 다시 켜요. \
            백업 이후 질문 기록은 화면에서 사라지지만, 지금 DB는 데이터 폴더에 before-restore 이름으로 남아요.
            """)
        }
    }

    private func row(_ b: BackupItem) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(date(b.mtime)).font(.callout.monospacedDigit())
                Text("\(b.kindLabel) · \(String(format: "%.1f", Double(b.bytes) / 1e6))MB"
                     + (b.queries.map { " · 질문 \($0)건" } ?? ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            sha(b.sha256_ok)
        }
    }

    @ViewBuilder
    private func sha(_ ok: Bool?) -> some View {
        switch ok {
        case .some(true): Label("sha256 일치", systemImage: "checkmark.seal").font(.caption).foregroundStyle(.green)
        case .some(false): Label("sha256 불일치", systemImage: "xmark.seal").font(.caption).foregroundStyle(.red)
        case .none: Text("sha256 기록 없음").font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var checkBox: some View {
        if m.checking {
            HStack(spacing: 6) { ProgressView().controlSize(.small); Text("백업 확인 중").font(.caption) }
        } else if let c = m.check {
            VStack(alignment: .leading, spacing: 3) {
                if c.ok {
                    Label("복구할 수 있어요 — 무결성 \(c.integrity ?? "-") · 스키마 v\(c.schema_version ?? 0) · 질문 \(c.queries ?? 0)건 · 마지막 질문 \(c.latest ?? "-")",
                          systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    if c.sha256_ok == nil {
                        Text("sha256 기록이 없어 파일이 온전히 복사됐는지는 확인하지 못했어요(무결성 검사는 통과).")
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Label("이 백업으로는 복구할 수 없어요", systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                    ForEach((c.problems ?? []) + (c.error.map { [$0] } ?? []), id: \.self) { Text("· " + $0) }
                }
            }
            .font(.caption).fixedSize(horizontal: false, vertical: true)
        } else {
            Text("목록에서 백업을 고르면 먼저 무결성과 sha256를 확인해요.").font(.caption).foregroundStyle(.secondary)
        }
    }

    private var done: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let r = m.result, r.ok {
                Label("복구했어요 — 질문 \(r.queries ?? 0)건 · 마지막 질문 \(r.latest ?? "-")", systemImage: "checkmark.circle.fill")
                    .font(.headline).foregroundStyle(.green)
                if let a = r.moved_aside?.first {
                    Text("이전 DB: \(a)").font(.caption).textSelection(.enabled)
                }
                if let p = r.pre_restore_backup {
                    Text("복구 직전 백업: \(p)").font(.caption).textSelection(.enabled)
                }
                if let e = r.pre_restore_error {
                    Text("복구 직전 백업은 실패했어요(\(e)). 옮겨 둔 이전 DB가 원본이에요.").font(.caption).foregroundStyle(.orange)
                }
            } else {
                Label("복구하지 못했어요", systemImage: "xmark.octagon.fill").font(.headline).foregroundStyle(.red)
                Text(m.result?.error ?? "알 수 없는 오류").font(.caption).fixedSize(horizontal: false, vertical: true)
                Text("DB는 바뀌지 않았어요. 봇·콘솔은 원래대로 다시 켰어요.").font(.caption).foregroundStyle(.secondary)
            }
            Text("점검 결과").font(.subheadline).padding(.top, 4)
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(m.doctor) { d in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: d.symbol).foregroundStyle(d.color)
                            Text(d.name).font(.caption.weight(.semibold))
                            Text(d.detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if m.doctor.isEmpty { Text("점검 결과를 읽지 못했어요.").font(.caption).foregroundStyle(.secondary) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button("다른 백업 고르기") { m.phase = .choose; m.result = nil; m.check = nil; m.picked = nil }
                Spacer()
                Button("닫기") { NSApp.keyWindow?.close() }.buttonStyle(.borderedProminent)
            }
        }
    }

    private func date(_ ts: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.string(from: Date(timeIntervalSince1970: ts))
    }
}
