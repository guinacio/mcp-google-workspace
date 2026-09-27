/**
 * Per-view lifecycle bookkeeping: timers, in-flight requests, and generations.
 *
 * - Every timer and request the view starts is owned here, so teardown (or a
 *   superseding event) can cancel all of them.
 * - Generations make "latest wins" explicit: each kind of load (the main
 *   dashboard data, a detail panel, a navigation step) takes a ticket, and a
 *   response is applied only if its ticket is still the newest one of its kind.
 *   A slow earlier response can therefore never overwrite newer state.
 * - After `dispose()`, nothing new starts and every late response is ignored.
 */

export type LoadKind = "data" | "detail" | "navigation" | "calendars";

export class Ticket {
  constructor(
    private readonly owner: ViewLifecycle,
    readonly kind: LoadKind,
    readonly generation: number,
    readonly controller: AbortController,
  ) {}

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  /** Whether this response may still be applied. */
  get current(): boolean {
    return this.owner.isCurrent(this);
  }

  /** Release the request's abort controller once it settled. */
  done(): void {
    this.owner.release(this.controller);
  }
}

export class ViewLifecycle {
  private disposedFlag = false;
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();
  private readonly controllers = new Set<AbortController>();
  private readonly generations = new Map<LoadKind, number>();
  private readonly latest = new Map<LoadKind, Ticket>();

  get disposed(): boolean {
    return this.disposedFlag;
  }

  /** Run `callback` after `ms` unless cancelled or disposed. Returns a canceller. */
  schedule(callback: () => void, ms: number): () => void {
    if (this.disposedFlag) return () => {};
    const handle = setTimeout(() => {
      this.timers.delete(handle);
      if (!this.disposedFlag) callback();
    }, ms);
    this.timers.add(handle);
    return () => {
      clearTimeout(handle);
      this.timers.delete(handle);
    };
  }

  /**
   * Start a load of `kind`: supersedes (and aborts) the previous load of the same
   * kind and returns a ticket whose `current` flag guards the response.
   */
  begin(kind: LoadKind): Ticket {
    const generation = (this.generations.get(kind) ?? 0) + 1;
    this.generations.set(kind, generation);
    this.latest.get(kind)?.controller.abort();
    const controller = new AbortController();
    if (this.disposedFlag) controller.abort();
    this.controllers.add(controller);
    const ticket = new Ticket(this, kind, generation, controller);
    this.latest.set(kind, ticket);
    return ticket;
  }

  /** Invalidate outstanding loads of `kind` (e.g. an authoritative host result arrived). */
  supersede(kind: LoadKind): void {
    this.generations.set(kind, (this.generations.get(kind) ?? 0) + 1);
    this.latest.get(kind)?.controller.abort();
    this.latest.delete(kind);
  }

  isCurrent(ticket: Ticket): boolean {
    return !this.disposedFlag && this.generations.get(ticket.kind) === ticket.generation;
  }

  /** A signal for a one-off request that only teardown cancels. */
  request(): { signal: AbortSignal; done: () => void } {
    const controller = new AbortController();
    if (this.disposedFlag) controller.abort();
    this.controllers.add(controller);
    return { signal: controller.signal, done: () => this.release(controller) };
  }

  release(controller: AbortController): void {
    this.controllers.delete(controller);
  }

  dispose(): void {
    if (this.disposedFlag) return;
    this.disposedFlag = true;
    for (const handle of this.timers) clearTimeout(handle);
    this.timers.clear();
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    this.latest.clear();
  }
}
