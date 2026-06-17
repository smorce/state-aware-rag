use crate::errors::PortError;
use std::net::TcpListener;

pub const DEFAULT_PORT: u16 = 6969;
const MAX_PORT_ATTEMPTS: u16 = 100;

pub fn is_port_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

pub fn find_available_port(starting_port: u16) -> Result<u16, PortError> {
    for offset in 0..MAX_PORT_ATTEMPTS {
        let port = starting_port.saturating_add(offset);
        if is_port_available(port) {
            return Ok(port);
        }
    }

    Err(PortError::NoAvailablePort {
        start: starting_port,
        end: starting_port + MAX_PORT_ATTEMPTS - 1,
    })
}

pub fn ensure_port_available(requested_port: u16) -> Result<(u16, bool), PortError> {
    if is_port_available(requested_port) {
        return Ok((requested_port, false));
    }
    let available = find_available_port(requested_port + 1)?;
    Ok((available, true))
}
