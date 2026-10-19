// AskProject 메뉴 막대 앱.
// 화면만 맡고, 동작은 백엔드 CLI(`status --json`, `config get|set --json`, `setup …`, `doctor --json`)를 부른다.
// 배포 앱은 번들에 백엔드가 들어 있고(Contents/Resources/backend), 상시 작업은 SMAppService로 등록한다.

import AppKit
import Combine
import SwiftUI

/// 마법사·설정 창. 메뉴 막대 전용 앱(LSUIElement)이라 SwiftUI Window 장면 대신 직접 띄운다(시작할 때 자동으로 열기 위해).
@MainActor
final class WindowManager {
    static let shared = WindowManager()
    private var wizard: NSWindow?
    private var settings: NSWindow?
    /// 창마다 제목을 만드는 함수. 언어를 바꾸면 열린 창 제목을 다시 단다.
    private var titles: [ObjectIdentifier: (NSWindow, () -> String)] = [:]
    private var langWatch: AnyCancellable?

    private init() {
        langWatch = Localizer.shared.$choice.dropFirst().receive(on: DispatchQueue.main).sink { [weak self] _ in
            self?.titles.values.forEach { $0.0.title = $0.1() }
        }
    }

    func showWizard(step: WizardModel.Step? = nil) {
        if let step {  // 원하는 단계로 다시 연다(마법사는 저장된 단계에서 시작한다)
            UserDefaults.standard.set(step.rawValue, forKey: "wizard.step")
            wizard?.close()
            wizard = nil
        }
        wizard = show(wizard, title: { L("AskProject 설정") }, view: WizardView())
    }

    private var privacy: NSWindow?

    func showPrivacy() {
        privacy = show(privacy, title: { L("데이터와 개인정보") }, view: PrivacyDetailView())
    }

    private var restore: NSWindow?

    func showRestore() {
        if let w = restore, !w.isVisible { restore = nil }  // 닫은 창은 새로 열어 목록을 다시 읽는다
        restore = show(restore, title: { L("백업에서 복구") }, view: RestoreView())
    }

    func closeWizard() {
        wizard?.close()
    }

    func showSettings(tab: SettingsTab = .connections) {
        if settings != nil { settings?.close(); settings = nil }
        SettingsView.lastTab = tab
        settings = show(settings, title: { L("AskProject 설정") }, view: SettingsView(tab: tab))
    }

    private func show<V: View>(_ existing: NSWindow?, title: @escaping () -> String, view: V) -> NSWindow {
        if let w = existing, w.isVisible {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return w
        }
        let w = NSWindow(contentViewController: NSHostingController(rootView: LocalizedRoot { view }))
        w.title = title()
        titles = titles.filter { $0.value.0.isVisible }
        titles[ObjectIdentifier(w)] = (w, title)
        w.styleMask = [.titled, .closable, .miniaturizable]
        w.isReleasedWhenClosed = false
        w.center()
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return w
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard Backend.isBundled else { return }  // 개발 앱은 레포 경로에서 그대로 돈다
        if offerMoveToApplications() { return }
        Task { @MainActor in
            await afterUpdateIfNeeded()
            Updater.shared.start()
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            let model = AppModel.shared
            if model.status?.configured == false && !UserDefaults.standard.bool(forKey: "wizard.done") {
                WindowManager.shared.showWizard()
            }
        }
    }

    /// 응용 프로그램 폴더 밖(다운로드·DMG·App Translocation)에서 실행 중이면 옮기기를 권한다.
    /// 번역 위치(무작위 읽기 전용 경로)에서 SMAppService에 등록하면 재부팅 뒤 경로가 사라진다.
    @MainActor
    private func offerMoveToApplications() -> Bool {
        let path = Bundle.main.bundlePath
        if path.hasPrefix("/Applications/") { return false }
        let alert = NSAlert()
        alert.messageText = L("AskProject를 응용 프로그램 폴더로 옮길까요?")
        alert.informativeText = L("봇이 이 맥에서 계속 돌려면 응용 프로그램 폴더에 있어야 해요. 옮긴 뒤 다시 열어요.")
        alert.addButton(withTitle: L("옮기기"))
        alert.addButton(withTitle: L("나중에"))
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        let dest = "/Applications/AskProject.app"
        do {
            let fm = FileManager.default
            if fm.fileExists(atPath: dest) {
                try fm.trashItem(at: URL(fileURLWithPath: dest), resultingItemURL: nil)
            }
            try fm.copyItem(atPath: path, toPath: dest)
            // 다운로드 격리 표시를 지운다(공증된 앱). 남아 있으면 옮긴 뒤에도 번역 위치에서 실행된다
            let x = Process()
            x.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
            x.arguments = ["-dr", "com.apple.quarantine", dest]
            try x.run()
            x.waitUntilExit()
            NSWorkspace.shared.openApplication(at: URL(fileURLWithPath: dest), configuration: .init()) { _, _ in
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
            return true
        } catch {
            let fail = NSAlert()
            fail.messageText = L("옮기지 못했어요")
            fail.informativeText = error.localizedDescription + "\n" + L("Finder에서 AskProject를 응용 프로그램 폴더로 끌어다 놓은 뒤 다시 여세요.")
            fail.runModal()
            return false
        }
    }

    /// 새 버전 첫 실행: 스키마·설정 마이그레이션(백업 후) → 업데이트 때 멈춘 작업을 새 번들로 다시 띄운다.
    @MainActor
    private func afterUpdateIfNeeded() async {
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        let last = UserDefaults.standard.string(forKey: "lastRunBuild")
        guard last != build else { return }
        guard last != nil else {  // 첫 설치
            UserDefaults.standard.set(build, forKey: "lastRunBuild")
            return
        }
        let r = await Backend.run(["migrate"])
        if !r.ok {
            // 빌드 번호를 기록하지 않는다 — 다음 실행에서 마이그레이션과 재등록을 다시 시도한다
            AppModel.shared.message = L("업데이트 후 데이터 마이그레이션 실패: %@ — 봇을 켜지 않았어요. 진단 묶음을 보내 주세요.", r.errorLine)
            return
        }
        Agents.reloadEnabled()
        UserDefaults.standard.set(build, forKey: "lastRunBuild")
    }
}

@main
struct AskProjectBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel.shared

    var body: some Scene {
        MenuBarExtra {
            LocalizedRoot { PanelView(model: model) }
        } label: {
            Image(systemName: model.health.symbol)
            if !model.warnings.isEmpty {
                Text("!")
            } else if let s = model.status, s.check > 0 {
                Text("\(s.check)")
            }
        }
        .menuBarExtraStyle(.window)
    }
}
