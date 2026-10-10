//! lolclip (application de bureau) : démarre le moteur Python en arrière-plan (serveur local http://127.0.0.1:8765),
//! attend qu'il réponde, puis affiche son interface dans la fenêtre de l'application. Le moteur est arrêté à la
//! fermeture de la fenêtre.
//!
//! - version de développement (`cargo tauri dev`) : `python lolclip.py` depuis la racine du dépôt ;
//! - version installée : `backend/lolclip-backend.exe` (moteur empaqueté par PyInstaller, voir packaging/).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
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

/// Requête HTTP minimale au moteur local -> corps de la réponse.
fn http(method: &str, path: &str) -> Option<String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port()));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(3))).ok()?;
    write!(s, "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n").ok()?;
    let mut out = String::new();
    s.read_to_string(&mut out).ok()?;
    out.split_once("\r\n\r\n").map(|(_, body)| body.to_string())
}

/// Version du moteur qui écoute déjà sur le port (None : pas de réponse, ou ancien moteur sans /api/version).
fn running_version() -> Option<String> {
    let body = http("GET", "/api/version")?;
    let v: serde_json::Value = serde_json::from_str(&body).ok()?;
    v.get("version")?.as_str().map(str::to_string)
}

/// PID du processus qui écoute sur le port (netstat), pour arrêter un ancien moteur qui ne sait pas s'arrêter seul.
#[cfg(windows)]
fn listener_pid() -> Option<u32> {
    let out = Command::new("netstat").args(["-ano", "-p", "TCP"]).creation_flags(CREATE_NO_WINDOW).output().ok()?;
    let local = format!("127.0.0.1:{}", port());
    String::from_utf8_lossy(&out.stdout).lines().find_map(|l| {
        let f: Vec<&str> = l.split_whitespace().collect();
        (f.len() >= 5 && f[1] == local && f[3] == "LISTENING").then(|| f[4].parse().ok()).flatten()
    })
}

/// Un moteur d'une autre version tourne déjà (ex. lolclip.bat lancé avant une mise à jour) : on l'arrête, sinon
/// l'appli afficherait l'ancienne interface avec l'ancien code. Seuls un moteur lolclip (python / exécutable) sont
/// arrêtés ; un autre programme sur le port -> erreur.
fn replace_stale_backend() -> Result<(), String> {
    let _ = http("POST", "/api/shutdown"); // moteurs récents : arrêt propre
    let t0 = Instant::now();
    while port_open() && t0.elapsed() < Duration::from_secs(3) {
        thread::sleep(Duration::from_millis(200));
    }
    #[cfg(windows)]
    if port_open() {
        let pid = listener_pid().ok_or("le port du moteur est occupé")?;
        let out = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| e.to_string())?;
        let name = String::from_utf8_lossy(&out.stdout).to_lowercase();
        if !(name.contains("python") || name.contains("lolclip-backend")) {
            return Err(format!("le port {} est utilisé par un autre programme : {}", port(), name.trim()));
        }
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
        let t0 = Instant::now();
        while port_open() && t0.elapsed() < Duration::from_secs(5) {
            thread::sleep(Duration::from_millis(200));
        }
    }
    if port_open() {
        return Err("impossible d'arrêter l'ancien moteur lolclip (ferme lolclip.bat)".into());
    }
    Ok(())
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
        if running_version().as_deref() == Some(env!("CARGO_PKG_VERSION")) {
            return Ok(None); // même version déjà lancée (autre fenêtre, ou lolclip.bat à jour) : on s'en sert
        }
        replace_stale_backend()?;
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
