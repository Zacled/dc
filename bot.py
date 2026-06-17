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
    def __init__(self, product: dict, coin: str, meta: dict, *, row: int, multi: bool) -> None:
        # With more than one product, show which product each button buys.
        label = f"{product['name']} · {meta['name']}" if multi else f"Pay with {meta['name']}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"buy_{product['id']}_{coin.lower()}",
            emoji=COIN_EMOJI.get(coin),
            row=row,
        )
        self.coin = coin
        self.product = product

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_ticket(interaction, self.coin, self.product)


class OtherButton(discord.ui.Button):
    def __init__(self, row: int = 1) -> None:
        super().__init__(
            label="Pay with another method",
            style=discord.ButtonStyle.secondary,
            custom_id="buy_other",
            emoji="💬",
            row=row,  # sits on its own row, below the coin buttons
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_other_ticket(interaction)


class PurchasePanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        enabled = config.enabled_coins()
        products = config.PRODUCTS
        multi = len(products) > 1
        # One row of coin buttons per product (only coins with an address show).
        for index, product in enumerate(products):
            row = min(index, 3)
            for coin, meta in enabled.items():
                self.add_item(BuyButton(product, coin, meta, row=row, multi=multi))
        # A catch-all "other payment method" ticket below the coin buttons.
        self.add_item(OtherButton(row=min(len(products), 4)))


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

        # Once a payment has been sent/confirmed, don't let the buyer close the
        # ticket — they could accidentally close it before getting their item.
        # Staff can still close it (after delivering).
        if (
            is_buyer
            and not is_staff
            and order is not None
            and order["status"] in ("detected", "paid")
        ):
            await interaction.response.send_message(
                "💸 Your payment is being processed — please **wait for staff** to "
                "deliver your item before closing. A staff member will close this "
                "ticket once you're sorted.",
                ephemeral=True,
            )
            return

        if order is not None and order["status"] in ("pending", "detected"):
            db.set_status(order["id"], "cancelled")
        await interaction.response.send_message("Saving transcript and closing…")
        await interaction.client._post_transcript(interaction.channel, interaction.user)
        await interaction.channel.delete(reason="Ticket closed")


# --- Support ticket views --------------------------------------------------
class ReasonButton(discord.ui.Button):
    def __init__(self, index: int, reason: str) -> None:
        super().__init__(
            label=reason[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"support_reason_{index}",
            row=index // 5,  # up to 5 buttons per row
        )
        self.reason = reason

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.open_support_ticket(interaction, self.reason)


class SupportPanel(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        # One button per reason (Discord allows up to 25 buttons / 5 rows).
        for index, reason in enumerate(config.SUPPORT_REASONS[:25]):
            self.add_item(ReasonButton(index, reason))


class SupportTicketControls(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.danger,
        custom_id="support_close",
        emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        ticket = db.get_support_ticket_by_channel(interaction.channel_id)
        is_staff = interaction.user.id == config.OWNER_ID or (
            config.SUPPORT_ROLE_ID
            and any(r.id == config.SUPPORT_ROLE_ID for r in getattr(interaction.user, "roles", []))
        )
        is_opener = ticket is not None and interaction.user.id == ticket["user_id"]
        if not (is_staff or is_opener):
            await interaction.response.send_message(
                "Only the person who opened this ticket or staff can close it.",
                ephemeral=True,
            )
            return
        if ticket is not None:
            db.set_support_status(ticket["id"], "closed")
        await interaction.response.send_message("Saving transcript and closing…")
        await interaction.client._post_transcript(interaction.channel, interaction.user)
        await interaction.channel.delete(reason="Support ticket closed")


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
        self.add_view(SupportPanel())
        self.add_view(SupportTicketControls())

        self.tree.add_command(panel_command)
        self.tree.add_command(support_panel_command)
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
        log.info(
            "Support role to ping: %s",
            config.SUPPORT_ROLE_ID or "NONE (set STAFF_ROLE_ID; will ping owner)",
        )

    # --- Ticket creation ---------------------------------------------------
    async def _create_ticket_channel(self, guild, user, *, prefix: str, reason: str):
        """Create a private channel visible to the buyer, staff, and the bot."""
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
        return await guild.create_text_channel(
            name=sanitize_channel_name(f"{prefix}-{user.name}"),
            overwrites=overwrites,
            category=category,
            reason=reason,
        )

    async def _post_transcript(self, channel: discord.TextChannel, closed_by) -> None:
        """Save a ticket's messages to the transcript channel before deletion."""
        dest = None
        if config.TRANSCRIPT_CHANNEL_ID:
            dest = self.get_channel(config.TRANSCRIPT_CHANNEL_ID)
        if dest is None and channel.guild is not None:
            dest = discord.utils.get(
                channel.guild.text_channels, name=config.TRANSCRIPT_CHANNEL_NAME
            )
        if dest is None:
            return  # no transcript channel configured/found

        lines: list[str] = []
        try:
            async for msg in channel.history(limit=1000, oldest_first=True):
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
                content = msg.content or ""
                for embed in msg.embeds:
                    parts = [p for p in (embed.title, embed.description) if p]
                    if parts:
                        content += ("\n" if content else "") + " | ".join(parts)
                for att in msg.attachments:
                    content += ("\n" if content else "") + f"[attachment] {att.url}"
                lines.append(f"[{ts}] {msg.author}: {content}")
        except discord.HTTPException:
            return

        transcript = "\n".join(lines) or "(no messages)"
        file = discord.File(
            io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt"
        )
        embed = discord.Embed(title=f"📑 Transcript — #{channel.name}", color=0x99AAB5)
        embed.add_field(name="Closed by", value=str(closed_by), inline=True)
        embed.add_field(name="Messages", value=str(len(lines)), inline=True)
        try:
            await dest.send(embed=embed, file=file)
        except discord.HTTPException:
            log.warning("Couldn't post transcript to #%s", config.TRANSCRIPT_CHANNEL_NAME)

    async def open_other_ticket(self, interaction: discord.Interaction) -> None:
        """Open a manual ticket for a non-crypto / 'other' payment method."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # Reuse an existing open 'other' ticket instead of spamming new ones.
        for order in db.get_active_orders():
            if order["user_id"] == user.id and order["coin"] == "OTHER":
                channel = self.get_channel(order["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        channel = await self._create_ticket_channel(
            guild, user, prefix="other", reason=f"Other-payment ticket for {user}"
        )
        # Minimal order row so the buyer can close it and we don't reopen dupes.
        db.create_order(
            user.id, channel.id, "OTHER", "", config.PRODUCT_PRICE_USD, 0.0
        )

        embed = discord.Embed(
            title=f"💬 {config.PRODUCT_NAME} — Other payment method",
            description=(
                "Thanks! You've opened a ticket to pay another way. "
                "Staff will be with you shortly to arrange payment and delivery."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="Price", value=f"${config.PRODUCT_PRICE_USD:.2f} USD", inline=True)
        owner = f"<@{config.OWNER_ID}>" if config.OWNER_ID else ""
        await channel.send(
            content=f"{user.mention} {owner}".strip(),
            embed=embed,
            view=TicketControls(),
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        await interaction.followup.send(
            f"Your ticket is ready: {channel.mention}", ephemeral=True
        )

    async def open_support_ticket(
        self, interaction: discord.Interaction, reason: str
    ) -> None:
        """Open a private support ticket and ping the support/staff role."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # One open support ticket per user.
        for ticket in db.get_open_support_tickets():
            if ticket["user_id"] == user.id:
                channel = self.get_channel(ticket["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open support ticket: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        channel = await self._create_ticket_channel(
            guild, user, prefix="support", reason=f"Support ticket for {user}"
        )
        ticket_id = db.create_support_ticket(user.id, channel.id, reason)

        # Explicitly whitelist the specific role so the bot can ping it even if
        # the role isn't set to "mentionable" (and without needing the
        # Mention-All-Roles permission).
        allowed = discord.AllowedMentions(users=True)
        if config.SUPPORT_ROLE_ID:
            ping = f"<@&{config.SUPPORT_ROLE_ID}>"
            allowed = discord.AllowedMentions(
                users=True, roles=[discord.Object(id=config.SUPPORT_ROLE_ID)]
            )
        elif config.OWNER_ID:
            ping = f"<@{config.OWNER_ID}>"
        else:
            ping = ""

        embed = discord.Embed(
            title=f"🎫 Support Ticket #{ticket_id}",
            description=(
                f"**Reason:** {reason}\n\n"
                f"Thanks {user.mention}! Staff have been notified and will be with "
                "you shortly. Go ahead and describe your issue in detail."
            ),
            color=0x5865F2,
        )
        await channel.send(
            content=f"{user.mention} {ping}".strip(),
            embed=embed,
            view=SupportTicketControls(),
            allowed_mentions=allowed,
        )
        await interaction.followup.send(
            f"Your support ticket is open: {channel.mention}", ephemeral=True
        )

    async def open_ticket(
        self, interaction: discord.Interaction, coin: str, product: dict
    ) -> None:
        meta = config.COINS[coin]
        if not meta["address"]:
            await interaction.response.send_message(
                f"{meta['name']} payments aren't set up yet. Ping staff.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        price_usd = product["price_usd"]

        # Reuse an existing open ticket for the same product+coin (no spamming).
        for order in db.get_active_orders():
            if (
                order["user_id"] == user.id
                and order["coin"] == coin
                and order["product_name"] == product["name"]
            ):
                channel = self.get_channel(order["channel_id"])
                if channel is not None:
                    await interaction.followup.send(
                        f"You already have an open {coin} ticket for "
                        f"{product['name']}: {channel.mention}",
                        ephemeral=True,
                    )
                    return

        # Quote the live price.
        try:
            coin_amount, coin_price = await prices.usd_to_coin(
                self.session, meta["coingecko_id"], price_usd
            )
        except prices.PriceError as exc:
            await interaction.followup.send(
                f"Couldn't fetch the {coin} price right now: {exc}", ephemeral=True
            )
            return

        expected = payments.make_unique_amount(coin, coin_amount)

        channel = await self._create_ticket_channel(
            guild, user, prefix=f"{product['id']}-{coin}",
            reason=f"{product['name']} order ({coin}) for {user}",
        )

        order_id = db.create_order(
            user.id, channel.id, coin, meta["address"], price_usd, expected,
            product["name"],
        )

        amount_str = format_amount(expected, meta["decimals"])
        embed = discord.Embed(
            title=f"🧾 Order #{order_id} — {product['name']}",
            description=(
                f"Send **exactly** the amount below in **{meta['name']} ({coin})**.\n"
                "The amount is unique to your order — sending a different amount "
                "may not be detected automatically."
            ),
            color=meta["color"],
        )
        embed.add_field(name="Price", value=f"${price_usd:.2f} USD", inline=True)
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
                f"Order **#{order['id']}** for **{order['product_name'] or config.PRODUCT_NAME}** is paid.\n"
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
    product_lines = "\n".join(
        f"**{p['name']}** — ${p['price_usd']:.2f}" for p in config.PRODUCTS
    )
    title = config.PRODUCT_NAME if len(config.PRODUCTS) == 1 else "Shop"
    embed = discord.Embed(
        title=f"🛒 {title}",
        description=(
            f"{product_lines}\n\n"
            "Pick a product + payment method below. A private ticket will open "
            "with payment details. Once your payment is confirmed on-chain, staff "
            f"are automatically pinged to hand over your key.\n\n**Accepted:** {coin_list}"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Payments are detected automatically — send the exact amount shown.")
    await interaction.response.send_message(embed=embed, view=PurchasePanel())


@discord.app_commands.command(
    name="supportpanel", description="Post the support ticket panel (owner only)."
)
async def support_panel_command(interaction: discord.Interaction) -> None:
    if config.OWNER_ID and interaction.user.id != config.OWNER_ID:
        await interaction.response.send_message(
            "Only the owner can post the panel.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎫 Support",
        description=(
            "Need help? Click a button below to open a **private support ticket** "
            "with our staff. Please only open a ticket if you genuinely need help."
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="One ticket per person — staff will respond as soon as they can.")
    await interaction.response.send_message(embed=embed, view=SupportPanel())


def main() -> None:
    bot = PaymentBot()
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
