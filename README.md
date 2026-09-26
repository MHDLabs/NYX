# NYX

> A lightweight, terminal-native communication platform for developers and technical users.

NYX is an open-source experiment focused on building a simple messaging system around the terminal.

The idea is somewhere between **IRC, Signal, and modern messaging platforms**, but with a strong focus on terminal usage, simplicity, privacy, and developer-oriented workflows.

The project is intentionally being kept small. NYX is not trying to become a massive platform with every possible feature.

## Current Direction

The current direction of NYX is a clean rebuild around a few core ideas:

* Terminal-first user experience
* Lightweight architecture
* Rust-based client
* Language-independent server protocol
* Private and secure messaging
* Simple federation/server model where practical
* Developer-friendly communication
* Minimal dependencies and unnecessary complexity
* Extensible protocol instead of locking the ecosystem to one server language

The client is planned to be written in **Rust**, primarily because it provides a strong foundation for a fast, lightweight, portable terminal application.

The server side is intentionally **not tied to one programming language**. A server implementation could be written in PHP, Python, JavaScript/TypeScript, Rust, or another suitable language as long as it follows the NYX protocol.

## Terminal First

The terminal is not just another interface for NYX.

It is the primary interface.

The client is designed around the kind of workflow where a user can open a terminal, connect to a server, communicate with others, and manage the application without needing a graphical desktop environment.

The TUI direction is based on the strongest parts of the previous NYX prototypes, while the previous experimental architecture is not being carried forward as-is.

## Protocol First

NYX aims to define a clear protocol between clients and servers rather than forcing everyone to use the same backend implementation.

This means different server implementations could coexist:

```text
             NYX Protocol
                  │
       ┌──────────┼──────────┐
       │          │          │
     PHP       Python     JavaScript
       │          │          │
     Server     Server     Server
```

The protocol is the common layer.

This makes it possible to build different server implementations without changing the client itself.

## Security & Privacy

Security is an important part of the project, but NYX does not claim to provide perfect security, perfect anonymity, or impossible-to-break infrastructure.

The project will prefer established cryptographic approaches and simple, understandable security boundaries over custom cryptography or unnecessarily complicated systems.

Features such as encryption, identity verification, server trust, and abuse protection will be introduced only when they can be designed and implemented realistically.

## Optional Ecosystem Features

Some ideas that appeared in earlier NYX designs are intentionally not part of the current core architecture.

For example, reputation, AI, decentralized infrastructure, or other specialized capabilities may eventually exist as **optional users, services, extensions, or protocol participants with specific roles/tags**, rather than becoming mandatory parts of the entire system.

The goal is to keep the core communication system small.

## Documentation

### Whitepaper

The current architectural and technical vision is documented in the NYX whitepaper.

**[Read the Whitepaper](index.html)**

The whitepaper is an interactive HTML document designed to be read directly in a browser and is also suitable for printing or exporting to PDF.

### Presentation

A short English presentation covering the project and its architecture is also available.

**[View the Presentation](slides.pdf)**

## Roadmap

NYX is currently in a **research and redesign phase**.

The immediate goal is not to rush into implementation. The architecture, protocol boundaries, terminal interface, and project scope need to be settled first.

A simplified development direction is:

```text
Research & Design
       │
       ▼
Protocol Definition
       │
       ▼
Rust TUI Client
       │
       ▼
Minimal Server
       │
       ▼
Messaging & Identity
       │
       ▼
Security Hardening
       │
       ▼
Federation / Extensions
```

The roadmap is intentionally flexible. Features will be added only when they have a clear purpose and can be implemented without turning the project into unnecessary infrastructure.

## Project Status

**NYX is currently paused.**

The project is being put on hold while the current design is reviewed and other work takes priority.

Development may remain paused for an extended period, potentially for **up to a year or more**.

This repository therefore represents the current project direction and documentation, not a promise of active development.

When development resumes, the implementation will be built around the simplified architecture described in the current documentation rather than attempting to preserve every feature from previous prototypes.

## Previous Prototypes

NYX has gone through several experimental versions and architectural approaches.

Those versions were useful for exploring ideas, but they are **not considered the foundation of the current implementation**.

The current direction intentionally starts from a cleaner and smaller architecture, while retaining useful lessons from the previous terminal interface experiments.

## License

This project is open source. See the repository license for details.
