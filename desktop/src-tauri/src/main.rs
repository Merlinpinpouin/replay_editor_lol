//! lolclip (application de bureau) : démarre le moteur Python en arrière-plan (serveur local http://127.0.0.1:8765),
//! attend qu'il réponde, puis affiche son interface dans la fenêtre de l'application. Le moteur est arrêté à la
//! fermeture de la fenêtre.
//!
//! - version de développement (`cargo tauri dev`) : `python lolclip.py` depuis la racine du dépôt ;
//! - version installée : `backend/lolclip-backend.exe` (moteur empaqueté par PyInstaller, voir packaging/).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const DEFAULT_PORT: u16 = 8765;

/// Port du moteur : 8765, ou LOLCLIP_PORT (tests, transmis au moteur avec le reste de l'environnement).
fn port() -> u16 {
    std::env::var("LOLCLIP_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(DEFAULT_PORT)
}
const START_TIMEOUT: Duration = Duration::from_secs(90);
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Processus du moteur, s'il a été lancé par l'application (sinon un moteur tournait déjà : on s'en sert).
struct Backend(Mutex<Option<Child>>);

fn port_open() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port()));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn backend_command(app: &AppHandle) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let mut cmd = Command::new("python");
        cmd.arg(root.join("lolclip.py")).current_dir(&root);
        return Ok(cmd);
    }
    let dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("dossier de l'application introuvable : {e}"))?
        .join("backend");
    let exe = dir.join("lolclip-backend.exe");
    if !exe.exists() {
        return Err(format!("moteur introuvable : {}", exe.display()));
    }
    let mut cmd = Command::new(exe);
    cmd.current_dir(&dir);
    Ok(cmd)
}

fn start_backend(app: &AppHandle) -> Result<Option<Child>, String> {
    if port_open() {
        return Ok(None); // déjà lancé (lolclip.bat, ou une autre fenêtre)
    }
    let mut cmd = backend_command(app)?;
    cmd.env("LOLCLIP_NOBROWSER", "1");
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.spawn()
        .map(Some)
        .map_err(|e| format!("impossible de lancer le moteur : {e}"))
}

/// Arrête le moteur et tout ce qu'il a lancé (ffmpeg, enregistrement du son, écran de repérage).
fn stop_backend(app: &AppHandle) {
    let state = app.state::<Backend>();
    let child = state.0.lock().ok().and_then(|mut c| c.take());
    if let Some(mut child) = child {
        #[cfg(windows)]
        {
            let _ = Command::new("taskkill")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn show_error(window: &WebviewWindow, msg: &str) {
    let msg = serde_json::to_string(msg).unwrap_or_default();
    let _ = window.eval(&format!("window.showError && window.showError({msg})"));
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("lolclip")
                .inner_size(1120.0, 900.0)
                .min_inner_size(720.0, 560.0)
                // glisser-déposer géré par la page (dépôt du .rofl ou d'une musique), pas par Tauri
                .disable_drag_drop_handler()
                .build()?;

            let handle = app.handle().clone();
            match start_backend(&handle) {
                Ok(child) => *app.state::<Backend>().0.lock().unwrap() = child,
                Err(e) => {
                    show_error(&window, &e);
                    return Ok(());
                }
            }
            thread::spawn(move || {
                let t0 = Instant::now();
                while !port_open() {
                    if t0.elapsed() > START_TIMEOUT {
                        show_error(&window, "le moteur ne répond pas (voir run/backend.log dans le dossier des données)");
                        return;
                    }
                    thread::sleep(Duration::from_millis(250));
                }
                let url = Url::parse(&format!("http://127.0.0.1:{}/", port())).expect("url");
                let _ = window.navigate(url);
                let _ = handle; // garde l'application vivante pendant l'attente
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("erreur au démarrage de l'application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}
