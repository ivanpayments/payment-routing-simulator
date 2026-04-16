"""payment-router CLI - entry point: `payment-router`."""

from __future__ import annotations

import json
import sys
from typing import Optional

import click

from payment_router import __version__
from payment_router.provider_loader import list_providers, load_provider
from payment_router.response_codes import lookup_bin


@click.group()
@click.version_option(__version__, prog_name="payment-router")
def main() -> None:
    """payment-router: simulate payment provider behaviour for integration testing."""


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

@main.command()
@click.option("--provider", "-p", required=True, help="Provider name (e.g. stripe, adyen)")
@click.option("--country", "-c", required=True, help="ISO 3166-1 alpha-2 merchant country code (e.g. BR)")
@click.option("--issuer-country", "issuer_country", default=None,
              help="Card-issuing country (omit = domestic). E.g. --issuer-country NG")
@click.option("--card", default="visa", show_default=True,
              type=click.Choice(["visa", "mastercard", "amex", "discover", "jcb", "unionpay"]),
              help="Card brand")
@click.option("--card-type", "card_type", default="credit", show_default=True,
              type=click.Choice(["credit", "debit", "prepaid", "commercial"]),
              help="Card funding type")
@click.option("--amount", "-a", required=True, type=float, help="Transaction amount")
@click.option("--currency", default="USD", show_default=True, help="ISO 4217 currency code")
@click.option("--3ds", "use_3ds", is_flag=True, default=False, help="Simulate 3DS flow")
@click.option("--runs", "-n", default=1, show_default=True, type=int,
              help="Number of simulation runs (>1 for distribution view)")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
def simulate(
    provider: str,
    country: str,
    issuer_country: Optional[str],
    card: str,
    card_type: str,
    amount: float,
    currency: str,
    use_3ds: bool,
    runs: int,
    output_json: bool,
) -> None:
    """Simulate a payment transaction against a provider."""
    from payment_router.engine import simulate_transaction
    from payment_router.models import CardBrand, CardType, SimulateRequest

    req = SimulateRequest(
        provider=provider,
        country=country,
        issuer_country=issuer_country,
        card_brand=CardBrand(card),
        card_type=CardType(card_type),
        amount=amount,
        currency=currency,
        use_3ds=use_3ds,
    )

    results = [simulate_transaction(req) for _ in range(runs)]

    if output_json:
        output = [r.model_dump() for r in results]
        click.echo(json.dumps(output if runs > 1 else output[0], indent=2))
        return

    if runs == 1:
        r = results[0]
        status = click.style("APPROVED", fg="green") if r.approved else click.style("DECLINED", fg="red")
        click.echo(f"\n  Provider : {r.provider}")
        click.echo(f"  Status   : {status}")
        click.echo(f"  Code     : {r.response_code} - {r.response_message}")
        if r.merchant_advice_code:
            click.echo(f"  Advice   : {r.merchant_advice_code}")
        click.echo(f"  Latency  : {r.latency_ms:.0f} ms")
        if r.three_ds:
            click.echo(f"  3DS      : v{r.three_ds.version.value} | challenged={r.three_ds.challenged} | "
                       f"pares={r.three_ds.pares_status.value}")
        click.echo()
    else:
        approved = sum(1 for r in results if r.approved)
        latencies = [r.latency_ms for r in results]
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        click.echo(f"\n  Runs     : {runs}")
        click.echo(f"  Approved : {approved}/{runs} ({approved / runs:.1%})")
        click.echo(f"  Latency  : p50={p50:.0f}ms  p95={p95:.0f}ms")
        click.echo()


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

@main.command()
@click.option("--country", "-c", required=True, help="ISO 3166-1 alpha-2 merchant country code")
@click.option("--issuer-country", "issuer_country", default=None,
              help="Card-issuing country (omit = domestic)")
@click.option("--card", default="visa", show_default=True,
              type=click.Choice(["visa", "mastercard", "amex", "discover", "jcb", "unionpay"]))
@click.option("--amount", "-a", required=True, type=float)
@click.option("--currency", default="USD", show_default=True)
@click.option("--3ds", "use_3ds", is_flag=True, default=False)
@click.option("--json", "output_json", is_flag=True, default=False)
def compare(
    country: str,
    issuer_country: Optional[str],
    card: str,
    amount: float,
    currency: str,
    use_3ds: bool,
    output_json: bool,
) -> None:
    """Compare all providers for a given transaction profile."""
    from payment_router.engine import compare_providers
    from payment_router.models import CardBrand, CompareRequest

    req = CompareRequest(
        country=country,
        issuer_country=issuer_country,
        card_brand=CardBrand(card),
        amount=amount,
        currency=currency,
        use_3ds=use_3ds,
    )

    results = compare_providers(req)

    if output_json:
        click.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    click.echo(f"\n  Comparing {len(results)} providers - {country} / {card} / {amount} {currency}\n")
    header = f"  {'Provider':<16} {'Approval':>9} {'p50 ms':>8} {'p95 ms':>8}"
    click.echo(header)
    click.echo("  " + "-" * (len(header) - 2))
    for r in results:
        bar = "#" * int(r.projected_approval_rate * 20)
        click.echo(
            f"  {r.provider:<16} {r.projected_approval_rate:>8.1%} "
            f"{r.latency_p50_ms:>8.0f} {r.latency_p95_ms:>8.0f}  {bar}"
        )
    click.echo()


# ---------------------------------------------------------------------------
# route  (retry cascade across providers)
# ---------------------------------------------------------------------------

@main.command()
@click.option("--provider", "-p", "providers", multiple=True, required=True,
              help="Providers in priority order (repeat flag for each, e.g. -p a -p b)")
@click.option("--country", "-c", required=True, help="ISO 3166-1 alpha-2 country code")
@click.option("--card", default="visa", show_default=True,
              type=click.Choice(["visa", "mastercard", "amex", "discover", "jcb", "unionpay"]))
@click.option("--card-type", "card_type", default="credit", show_default=True,
              type=click.Choice(["credit", "debit", "prepaid", "commercial"]))
@click.option("--amount", "-a", required=True, type=float)
@click.option("--currency", default="USD", show_default=True)
@click.option("--3ds", "use_3ds", is_flag=True, default=False)
@click.option("--json", "output_json", is_flag=True, default=False)
def route(
    providers: tuple[str, ...],
    country: str,
    card: str,
    card_type: str,
    amount: float,
    currency: str,
    use_3ds: bool,
    output_json: bool,
) -> None:
    """Route a transaction with soft-decline retry cascade across providers.

    Tries providers left to right. On a soft decline (retryable) it cascades
    to the next provider. On approval or hard decline it stops.

    Example: payment-router route -p global-acquirer-a -p regional-bank -c BR -a 300
    """
    from payment_router.engine import simulate_with_retry
    from payment_router.models import CardBrand, CardType, SimulateRequest

    req = SimulateRequest(
        provider=providers[0],
        country=country,
        card_brand=CardBrand(card),
        card_type=CardType(card_type),
        amount=amount,
        currency=currency,
        use_3ds=use_3ds,
    )

    result = simulate_with_retry(req, list(providers))

    if output_json:
        click.echo(json.dumps(result.model_dump(), indent=2))
        return

    click.echo(f"\n  Routing {country} / {card} / {card_type} / {amount} {currency}\n")
    for a in result.attempts:
        status = click.style("APPROVED", fg="green") if a.approved else click.style("DECLINED", fg="red")
        flag = " [soft — cascading]" if a.was_soft_decline else ""
        click.echo(f"  Attempt {a.attempt}  {a.provider:<22} {status}  code={a.response_code}  {a.latency_ms:.0f}ms{flag}")

    click.echo()
    outcome = click.style("SUCCESS", fg="green") if result.succeeded else click.style("FAILED", fg="red")
    click.echo(f"  Outcome  : {outcome}")
    click.echo(f"  Provider : {result.final_response.provider}")
    click.echo(f"  Total ms : {result.total_latency_ms:.0f}")
    if result.final_response.three_ds:
        t = result.final_response.three_ds
        shift = "issuer" if t.liability_shift else "merchant"
        click.echo(f"  3DS      : v{t.version.value} | challenged={t.challenged} | liability={shift}")
    click.echo()


# ---------------------------------------------------------------------------
# list-providers
# ---------------------------------------------------------------------------

@main.command("list-providers")
@click.option("--json", "output_json", is_flag=True, default=False)
def list_providers_cmd(output_json: bool) -> None:
    """List all available provider profiles."""
    providers = list_providers()
    if not providers:
        click.echo("No provider profiles found.")
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(providers))
        return

    click.echo("\n  Available providers:\n")
    for name in providers:
        try:
            p = load_provider(name)
            click.echo(f"    {name:<20} {p.display_name}")
        except Exception:
            click.echo(f"    {name:<20} (failed to load)")
    click.echo()


# ---------------------------------------------------------------------------
# bin-lookup
# ---------------------------------------------------------------------------

@main.command("bin-lookup")
@click.argument("bin_prefix")
def bin_lookup(bin_prefix: str) -> None:
    """Look up card brand, type, and issuing country for a BIN prefix."""
    brand, card_type, country = lookup_bin(bin_prefix)
    click.echo(f"\n  BIN      : {bin_prefix}")
    click.echo(f"  Brand    : {brand}")
    click.echo(f"  Type     : {card_type}")
    click.echo(f"  Country  : {country}\n")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

@main.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8090, show_default=True, type=int)
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code changes")
def server(host: str, port: int, reload: bool) -> None:
    """Start the payment-router REST API server."""
    import uvicorn
    uvicorn.run("payment_router.api:app", host=host, port=port, reload=reload)
