"""Crypto-payment ticket bot for Discord.

Flow:
  1. Owner runs /panel to post a purchase panel with LTC / SOL / ETH buttons.
  2. A buyer clicks a coin -> a private ticket channel is opened for them.
  3. The bot quotes the live $-price in that coin (with a unique amount), shows
     the receiving address + a QR code, and starts watching the chain.
  4. When the payment confirms, the bot pings the owner in the ticket so they
     can deliver the product key by hand.
"""

import io
import logging
import re

import aiohttp
import discord
import qrcode
from discord.ext import tasks

import config
import database as db
import payments
import prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


# --- Helpers ---------------------------------------------------------------
def format_amount(amount: float, decimals: int) -> str:
    """Exact, human-friendly amount string (no scientific notation)."""
    text = f"{amount:.{decimals}f}".rstrip("0").rstrip(".")
    return text or "0"


def payment_uri(coin: str, address: str, amount: float) -> str:
    """Build a wallet payment URI so QR scans can prefill the amount."""
    meta = config.COINS[coin]
    if coin == "ETH":
        wei = int(round(amount * 1e18))
        return f"ethereum:{address}?value={wei}"
    return f"{meta['uri_scheme']}:{address}?amount={amount:.{meta['decimals']}f}"


def make_qr_file(data: str) -> discord.File:
    img = qrcode.make(data)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="payment.png")


def sanitize_channel_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return cleaned[:90] or "buyer"


# --- Views (persistent across restarts) ------------------------------------
COIN_EMOJI = {"LTC": "🪙", "SOL": "🟣", "ETH": "💎"}


class BuyButton(discord.ui.Button):
    def __init__(self, coin: str, meta: dict) -> None:
        super().__init__(
            label=f"Pay with {meta['name']}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"buy_{coin.lower()}",
            emoji=COIN_EMOJI.get(coin),
        )
        self.coin = coin

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_ticket(interaction, self.coin)


class PurchasePanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        # Only show buttons for coins that actually have an address configured.
        for coin, meta in config.enabled_coins().items():
            self.add_item(BuyButton(coin, meta))


class TicketControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Check payment now",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_check",
        emoji="🔄",
    )
    async def check(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        order = db.get_order_by_channel(interaction.channel_id)
        if order is None:
            await interaction.followup.send("No order is linked to this ticket.", ephemeral=True)
            return
        if order["status"] == "paid":
            await interaction.followup.send("This order is already marked paid. ✅", ephemeral=True)
            return

        await interaction.client.run_scan()
        order = db.get_order(order["id"])
        messages = {
            "pending": "No payment seen yet. Send the exact amount and try again in a minute.",
            "detected": "Payment seen — waiting for confirmations. Hang tight.",
            "paid": "Payment confirmed! ✅ The owner has been pinged.",
            "expired": "This order expired. Please open a new ticket.",
        }
        await interaction.followup.send(
            messages.get(order["status"], "Still waiting…"), ephemeral=True
        )

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close",
        emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        order = db.get_order_by_channel(interaction.channel_id)
        is_staff = interaction.user.id == config.OWNER_ID or (
            config.STAFF_ROLE_ID
            and any(r.id == config.STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
        )
        is_buyer = order is not None and interaction.user.id == order["user_id"]
        if not (is_staff or is_buyer):
            await interaction.response.send_message(
                "Only the buyer or staff can close this ticket.", ephemeral=True
            )
            return

        if order is not None and order["status"] in ("pending", "detected"):
            db.set_status(order["id"], "cancelled")
        await interaction.response.send_message("Closing this ticket in 5 seconds…")
        await interaction.channel.delete(reason="Ticket closed")


# --- Bot -------------------------------------------------------------------
class PaymentBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        db.init_db()
        self.session = aiohttp.ClientSession()

        # Register persistent views so buttons keep working after a restart.
        self.add_view(PurchasePanel())
        self.add_view(TicketControls())

        self.tree.add_command(panel_command)
        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        self.payment_watcher.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        enabled = ", ".join(config.enabled_coins()) or "NONE (set addresses in .env!)"
        log.info("Accepting: %s", enabled)

    # --- Ticket creation ---------------------------------------------------
    async def open_ticket(self, interaction: discord.Interaction, coin: str) -> None:
        meta = config.COINS[coin]
        if not meta["address"]:
            await interaction.response.send_message(
                f"{meta['name']} payments aren't set up yet. Ping staff.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # Reuse an existing open ticket for the same coin instead of spamming.
        for order in db.get_active_orders():
            if order["user_id"] == user.id and order["coin"] == coin:
                channel = self.get_channel(order["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open {coin} ticket: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        # Quote the live price.
        try:
            coin_amount, coin_price = await prices.usd_to_coin(
                self.session, meta["coingecko_id"], config.PRODUCT_PRICE_USD
            )
        except prices.PriceError as exc:
            await interaction.followup.send(
                f"Couldn't fetch the {coin} price right now: {exc}", ephemeral=True
            )
            return

        expected = payments.make_unique_amount(coin, coin_amount)

        # Create the private ticket channel.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        if config.STAFF_ROLE_ID:
            staff_role = guild.get_role(config.STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        category = (
            guild.get_channel(config.TICKET_CATEGORY_ID)
            if config.TICKET_CATEGORY_ID
            else None
        )
        channel = await guild.create_text_channel(
            name=sanitize_channel_name(f"{coin}-{user.name}"),
            overwrites=overwrites,
            category=category,
            reason=f"Crypto order ({coin}) for {user}",
        )

        order_id = db.create_order(
            user.id, channel.id, coin, meta["address"], config.PRODUCT_PRICE_USD, expected
        )

        amount_str = format_amount(expected, meta["decimals"])
        embed = discord.Embed(
            title=f"🧾 Order #{order_id} — {config.PRODUCT_NAME}",
            description=(
                f"Send **exactly** the amount below in **{meta['name']} ({coin})**.\n"
                "The amount is unique to your order — sending a different amount "
                "may not be detected automatically."
            ),
            color=meta["color"],
        )
        embed.add_field(name="Price", value=f"${config.PRODUCT_PRICE_USD:.2f} USD", inline=True)
        embed.add_field(name=f"1 {coin}", value=f"≈ ${coin_price:,.2f}", inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(name=f"Amount to send ({coin})", value=f"```{amount_str}```", inline=False)
        embed.add_field(name="To this address", value=f"```{meta['address']}```", inline=False)
        embed.add_field(
            name="What happens next",
            value=(
                f"The bot is now watching the {coin} blockchain. Once your payment "
                f"confirms (~{meta['min_conf']} conf), staff are pinged to deliver "
                "your key. Use **Check payment now** to refresh."
            ),
            inline=False,
        )
        embed.set_image(url="attachment://payment.png")
        embed.set_footer(text=f"Expires in {config.ORDER_EXPIRY_MINUTES} min · Order #{order_id}")

        qr = make_qr_file(payment_uri(coin, meta["address"], expected))
        await channel.send(
            content=user.mention, embed=embed, file=qr, view=TicketControls()
        )
        await interaction.followup.send(
            f"Your {coin} ticket is ready: {channel.mention}", ephemeral=True
        )

    # --- Payment watching --------------------------------------------------
    async def run_scan(self) -> None:
        if self.session is None:
            return
        await payments.scan_once(
            self.session,
            on_detected=self._on_detected,
            on_confirmed=self._on_confirmed,
            on_expired=self._on_expired,
        )

    async def _on_detected(self, order, payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        meta = config.COINS[order["coin"]]
        embed = discord.Embed(
            title="⏳ Payment detected — confirming",
            description=(
                f"Saw **{format_amount(payment['amount'], meta['decimals'])} "
                f"{order['coin']}** ({payment['confirmations']}/{meta['min_conf']} "
                "confirmations). You'll be all set once it confirms."
            ),
            color=0xF1C40F,
        )
        await channel.send(embed=embed)

    async def _on_confirmed(self, order, payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        meta = config.COINS[order["coin"]]
        embed = discord.Embed(
            title="✅ Payment confirmed!",
            description=(
                f"Order **#{order['id']}** for **{config.PRODUCT_NAME}** is paid.\n"
                f"Amount: `{format_amount(payment['amount'], meta['decimals'])} "
                f"{order['coin']}`\nTx: `{payment['txid']}`"
            ),
            color=0x2ECC71,
        )
        owner = f"<@{config.OWNER_ID}>" if config.OWNER_ID else ""
        await channel.send(
            content=f"{owner} payment received — please deliver the key. "
            f"<@{order['user_id']}> you're all set!",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def _on_expired(self, order, _payment) -> None:
        channel = self.get_channel(order["channel_id"])
        if channel is None:
            return
        embed = discord.Embed(
            title="⌛ Order expired",
            description=(
                "No payment was detected in time. If you've already paid, ping "
                "staff with your transaction ID. Otherwise open a new ticket."
            ),
            color=0xE74C3C,
        )
        await channel.send(embed=embed)

    @tasks.loop(seconds=config.POLL_INTERVAL_SECONDS)
    async def payment_watcher(self) -> None:
        try:
            await self.run_scan()
        except Exception:  # noqa: BLE001 - keep the loop alive no matter what
            log.exception("payment_watcher pass failed")

    @payment_watcher.before_loop
    async def _before_watcher(self) -> None:
        await self.wait_until_ready()


# --- Slash command ---------------------------------------------------------
@discord.app_commands.command(
    name="panel", description="Post the crypto purchase panel (owner only)."
)
async def panel_command(interaction: discord.Interaction) -> None:
    if config.OWNER_ID and interaction.user.id != config.OWNER_ID:
        await interaction.response.send_message(
            "Only the owner can post the panel.", ephemeral=True
        )
        return

    enabled = config.enabled_coins()
    if not enabled:
        await interaction.response.send_message(
            "No coins are configured. Set LTC_ADDRESS / SOL_ADDRESS / ETH_ADDRESS "
            "in your .env first.",
            ephemeral=True,
        )
        return

    coin_list = ", ".join(meta["name"] for meta in enabled.values())
    embed = discord.Embed(
        title=f"🛒 {config.PRODUCT_NAME}",
        description=(
            f"**Price: ${config.PRODUCT_PRICE_USD:.2f}** (paid in crypto)\n\n"
            "Pick how you'd like to pay below. A private ticket will open with "
            "payment details. Once your payment is confirmed on-chain, staff are "
            f"automatically pinged to hand over your key.\n\n**Accepted:** {coin_list}"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Payments are detected automatically — send the exact amount shown.")
    await interaction.response.send_message(embed=embed, view=PurchasePanel())


def main() -> None:
    bot = PaymentBot()
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
