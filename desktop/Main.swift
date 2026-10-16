import AppKit
import WebKit

final class DesktopApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    var output = Data()
    var origin: URL?
    var logHandle: FileHandle?
    let defaults = UserDefaults.standard

    func dataDirectory() -> URL {
        if let testing = ProcessInfo.processInfo.environment["MEU_DINHEIRO_DATA_DIR"] { return URL(fileURLWithPath:testing,isDirectory:true) }
        if let selected = defaults.string(forKey:"dataDirectory") { return URL(fileURLWithPath:selected,isDirectory:true) }
        return FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("Meu Dinheiro",isDirectory:true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let main = NSMenu()
        let appItem = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(withTitle:"Sobre Meu Dinheiro",action:#selector(about),keyEquivalent:"")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle:"Abrir pasta dos dados",action:#selector(openData),keyEquivalent:"")
        appMenu.addItem(withTitle:"Usar outra pasta de dados…",action:#selector(chooseData),keyEquivalent:"")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle:"Sair do Meu Dinheiro",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        appItem.submenu=appMenu; main.addItem(appItem)
        let editItem=NSMenuItem(); let edit=NSMenu(title:"Editar")
        edit.addItem(withTitle:"Desfazer",action:Selector(("undo:")),keyEquivalent:"z")
        edit.addItem(withTitle:"Recortar",action:#selector(NSText.cut(_:)),keyEquivalent:"x")
        edit.addItem(withTitle:"Copiar",action:#selector(NSText.copy(_:)),keyEquivalent:"c")
        edit.addItem(withTitle:"Colar",action:#selector(NSText.paste(_:)),keyEquivalent:"v")
        edit.addItem(withTitle:"Selecionar tudo",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a")
        editItem.submenu=edit; main.addItem(editItem)
        let viewItem=NSMenuItem(); let view=NSMenu(title:"Visualizar")
        view.addItem(withTitle:"Recarregar",action:#selector(reload),keyEquivalent:"r")
        viewItem.submenu=view; main.addItem(viewItem)
        NSApp.mainMenu=main
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:1280,height:860),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title="Meu Dinheiro"; window.minSize=NSSize(width:420,height:600); window.center()
        let config=WKWebViewConfiguration(); config.websiteDataStore = .nonPersistent()
        web=WKWebView(frame:.zero,configuration:config); web.navigationDelegate=self; web.uiDelegate=self
        window.contentView=web; window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps:true)
        startBackend()
    }

    func startBackend() {
        let folder=dataDirectory()
        do {
            try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
            let executable=Bundle.main.resourceURL!.appendingPathComponent("backend/meu-dinheiro-server")
            let process=Process(); process.executableURL=executable
            process.arguments=["--port","0","--db",folder.appendingPathComponent("financas.db").path,"--no-browser"]
            let pipe=Pipe(); process.standardOutput=pipe
            let logURL=folder.appendingPathComponent("desktop.log")
            FileManager.default.createFile(atPath:logURL.path,contents:nil,attributes:[.posixPermissions:0o600])
            logHandle=try FileHandle(forWritingTo:logURL); process.standardError=logHandle
            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let chunk=handle.availableData
                if chunk.isEmpty { handle.readabilityHandler=nil; return }
                DispatchQueue.main.async {
                    guard let self=self else { return }
                    guard self.backend === process else { return }
                    self.output.append(chunk)
                    if let text=String(data:self.output,encoding:.utf8), let line=text.components(separatedBy:"\n").first(where:{$0.hasPrefix("Finanças disponível em ")}),
                       let url=URL(string:line.replacingOccurrences(of:"Finanças disponível em ",with:"")), self.origin == nil {
                        self.origin=url; self.web.load(URLRequest(url:url)); self.output.removeAll()
                    }
                }
            }
            process.terminationHandler = { [weak self] process in
                DispatchQueue.main.async { if process.terminationStatus != 0 { self?.showError("O serviço local encerrou. Confira desktop.log na pasta dos dados.") } }
            }
            backend=process; output.removeAll(); origin=nil
            web.loadHTMLString("<html><body style='font:18px system-ui;background:#f4f6fc;color:#293b64;padding:60px'><h1>Meu Dinheiro</h1><p>Preparando seu espaço financeiro local…</p></body></html>",baseURL:nil)
            try process.run()
        } catch { showError("Não foi possível iniciar o serviço local: \(error.localizedDescription)") }
    }

    func showError(_ text: String) { let alert=NSAlert(); alert.messageText="Meu Dinheiro"; alert.informativeText=text; alert.runModal() }
    @objc func about() { showError("Finanças locais, com contas separadas e demonstração fictícia.\nVersão 0.2.0 · prévia para macOS Apple Silicon.\nOs dados ficam na pasta escolhida neste Mac.") }
    @objc func reload() { web.reload() }
    @objc func openData() { NSWorkspace.shared.open(dataDirectory()) }
    @objc func chooseData() {
        let panel=NSOpenPanel(); panel.canChooseDirectories=true; panel.canChooseFiles=false; panel.allowsMultipleSelection=false
        panel.message="Escolha uma pasta de dados do Meu Dinheiro. Para usar o histórico do projeto, selecione sua pasta data. Os arquivos não são copiados para o aplicativo."
        if panel.runModal() == .OK, let folder=panel.url {
            stopBackend(); defaults.set(folder.path,forKey:"dataDirectory"); startBackend()
        }
    }
    func stopBackend() {
        if let process=backend, process.isRunning { process.terminationHandler=nil; process.terminate(); process.waitUntilExit() }
        backend=nil; try? logHandle?.close(); logHandle=nil
    }
    func applicationWillTerminate(_ notification: Notification) { stopBackend() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy)->Void) {
        guard let url=navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == origin?.port && url.scheme == "http") { decisionHandler(.allow); return }
        if navigationAction.navigationType == .linkActivated, ["https","http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url=navigationAction.request.url, ["https","http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if ProcessInfo.processInfo.environment["MEU_DINHEIRO_SMOKE_TEST"] == "1", origin != nil, webView.url?.host == "127.0.0.1" {
            webView.evaluateJavaScript("document.querySelector('#local-register-form') !== null && typeof localAuth !== 'undefined'") { result, error in
                if (result as? Bool) == true && error == nil { print("DESKTOP_SMOKE_OK"); NSApp.terminate(nil) }
            }
        }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message:String, initiatedByFrame frame:WKFrameInfo, completionHandler:@escaping (Bool)->Void) {
        let alert=NSAlert(); alert.messageText="Meu Dinheiro · confirmação"; alert.informativeText=message
        alert.addButton(withTitle:"Confirmar"); alert.addButton(withTitle:"Cancelar")
        alert.beginSheetModal(for:window) { completionHandler($0 == .alertFirstButtonReturn) }
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message:String, initiatedByFrame frame:WKFrameInfo, completionHandler:@escaping ()->Void) {
        let alert=NSAlert(); alert.messageText="Meu Dinheiro"; alert.informativeText=message
        alert.beginSheetModal(for:window) { _ in completionHandler() }
    }
    func webView(_ webView:WKWebView, runOpenPanelWith parameters:WKOpenPanelParameters, initiatedByFrame frame:WKFrameInfo, completionHandler:@escaping ([URL]?)->Void) {
        let panel=NSOpenPanel(); panel.allowsMultipleSelection=parameters.allowsMultipleSelection
        panel.canChooseDirectories=false; panel.canChooseFiles=true
        panel.beginSheetModal(for:window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }
}

let app=NSApplication.shared
let delegate=DesktopApp()
app.setActivationPolicy(.regular)
app.delegate=delegate
app.run()
