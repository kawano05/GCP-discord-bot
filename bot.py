import discord
from discord.ext import commands
from discord.ext.commands import has_permissions
from discord import app_commands
import sys
import asyncio
import asyncssh
import typing

#---------------------------------------------------------------------------
# ここから
# 自分のBotのアクセストークンに置き換えてください
TOKEN = 'YOUR_DISCORD_BOT_TOKEN'
# サービスアカウントID
SERVICE_ACCOUNT_ID='YOUR_SERVICE_ACCOUNT_ID'
# プロジェクト名
GCP_PROJECT_NAME='YOUR_PROJECT_NAME'
# VMインスタンスの名前
MINECRAFT_INSTANCE_NAME='YOUR_INSTANCE_NAME'
# VMインスタンスのゾーン
MINECRAFT_INSTANCE_ZONE='YOUR_INSTANCE_ZONE_NAME'
# VMのSSH接続情報
hostname = 'YOUR_VM_IP_ADDRESS'
username = 'YOUR_VM_USERNAME'
private_key_path = 'YOUR_PRIVATE_KEY_PATH'  # SSH秘密鍵のパス

# ゲーム名とModのリスト サンプルです
GAME_MODS = {
    "minecraft": ["vanilla_1.21.5"],
    "terraria": ["vanilla_expart", "calamity_andmore"],
}
# ここまでの情報を記述してください
# ここからは下は必要に応じて変更してください
#---------------------------------------------------------------------------
# DiscordのIntentsを設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# VMインスタンスの起動状態
VM_State = False
# それぞれのサーバーの起動状態
SERVER_STATE ={game:[False]*len(mods)for game, mods in GAME_MODS.items()}

# ここからがstartコマンドの記述
@bot.tree.command(name="start", description="ゲームサーバーを起動します")
@app_commands.describe(
    game="起動するゲーム名",
    mod="使用するMod (オプション)"
)

@app_commands.choices(game=[
    app_commands.Choice(name=game.capitalize(), value=game)
    for game in GAME_MODS.keys()
])


async def start_server(interaction: discord.Interaction, game: str, mod: str = None):
    await interaction.response.defer()
    
    global SERVER_STATE

    if game not in GAME_MODS:
        await interaction.followup.send(f"サポートされていないゲーム: {game}")
        return

    mod_index = GAME_MODS[game].index(mod) if mod else 0

    if SERVER_STATE[game][mod_index]:
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} サーバーは既に起動しています。")
        return

    if mod and mod not in GAME_MODS[game]:
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return

    true_count = sum(sum(states) for states in SERVER_STATE.values())
    if true_count == 3:
        await interaction.followup.send("サーバーを3つ開いているためこれ以上開けません")
        return

    mod_str = f" {mod}" if mod else ""
    # vmが起動しているかチェックと起動していなかったら起動
    result = await setup_server()
    if "起動します" in result:
        while await check_vm_state() == False:
            await asyncio.sleep(20)
            
        await asyncio.sleep(20)
    await interaction.followup.send(f"{result}")

    result = await execute_remote_script(True,game,mod)
    
    if "起動しました" in result:
        SERVER_STATE[game][mod_index] = True
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを起動しました。(立ち上げに数分かかることがあります)")
    else:
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーの起動に失敗しました。")
        if await check_all_server():
            await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
            await server_stop()
            await interaction.followup.send("VMを停止しました")

    # デバッグ用
    #if "エラー" in result:
    #    await interaction.followup.send(f"debug:{result}")

@start_server.autocomplete('mod')
async def mod_autocomplete(interaction: discord.Interaction, current: str):
    game = interaction.namespace.game
    if not game or game not in GAME_MODS:
        return []
    return [
        app_commands.Choice(name=mod, value=mod)
        for mod in GAME_MODS[game] if current.lower() in mod.lower()
    ]
# ここまでがstartコマンドの記述

# ここからstopコマンドの記述
@bot.tree.command(name="stop",description="ゲームサーバーを停止します")
@app_commands.describe(
    game="停止するゲーム名",
    mod="使用するMod(オプション)"
)

@app_commands.choices(game=[
    app_commands.Choice(name=game.capitalize(), value=game)
    for game in GAME_MODS.keys()
])

async def stop_server(interaction: discord.Interaction, game: str,mod: str = None):
    await interaction.response.defer()
    
    global SERVER_STATE

    if game not in GAME_MODS:
        await interaction.followup.send(f"サポートされていないゲーム: {game}")
        return

    mod_index = GAME_MODS[game].index(mod) if mod else 0
    mod_str = f" {mod}" if mod else "" 

    if SERVER_STATE[game][mod_index] == False:
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} サーバーは既に停止しています。")
        return

    if mod and mod not in GAME_MODS[game]:
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return
    
    await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを停止します(数分かかることがあります)")
    result = await execute_remote_script(False,game,mod)

    if "停止しました" in result:
        SERVER_STATE[game][mod_index] = False
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを停止しました。")
    else:
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーの停止に失敗しました。")

    # デバッグ用
    #if "エラー" in result:
    #    await interaction.followup.send(f"{result}")
    
    if await check_all_server():
        await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
        await server_stop()
        await interaction.followup.send("VMを停止しました")

@stop_server.autocomplete('mod')
async def mod_autocomplete(interaction: discord.Interaction, current: str):
    game = interaction.namespace.game
    if not game or game not in GAME_MODS:
        return []
    return [
        app_commands.Choice(name=mod, value=mod)
        for mod in GAME_MODS[game] if current.lower() in mod.lower()
    ]

# ここまでがstopコマンド

# ここからstateコマンド
@bot.tree.command(name="state", description="現在のVMとゲームサーバーの状態を表示します")
async def state_server(interaction: discord.Interaction):
    await interaction.response.defer()
    
    embed = discord.Embed(title="サーバー状態", color=discord.Color.blue())

    # VM状態
    vm_status = "🟢 起動中" if VM_State else "🔴 停止中"
    embed.add_field(name="VM状態", value=vm_status, inline=False)

    # サーバー状態
    for game, mods in GAME_MODS.items():
        game_status = []
        for i, mod in enumerate(mods):
            status = "🟢 起動中" if SERVER_STATE[game][i] else "🔴 停止中"
            mod_name = mod
            mod_name = f"`{mod_name:<24}`"  # 左寄せで20文字分のスペースを確保
            game_status.append(f"{mod_name}: {status}")

        # ゲームごとのステータスを表示
        embed.add_field(name=f"{game.capitalize()} サーバー", value="\n".join(game_status), inline=False)

    # フッターに現在時刻を追加
    embed.set_footer(text=f"最終更新: {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    await interaction.followup.send(embed=embed)
#ここまでがstateコマンド

#ここからresetコマンド
@bot.tree.command(name="reset", description="VMの強制停止と各サーバーの停止を行います(セーブされません,バグ対処用)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_server(interaction: discord.Interaction):
    await interaction.response.defer()
    global VM_State, SERVER_STATE
    VM_State = False
    SERVER_STATE = {game: [False] * len(mods) for game, mods in GAME_MODS.items()}
    await server_stop()
    await interaction.followup.send("VMを強制的に停止しました")

#ここまでresetコマンド

#ここからstate_setコマンド
@bot.tree.command(name="state_set",description="(基本使用しないでください)stateを手動で変更する。(サーバーエラー落ち用)")
@app_commands.describe(
    game="停止するゲーム名",
    mod="使用するMod(オプション)",
    state="代入する値"
)

@app_commands.choices(game=[
    app_commands.Choice(name=game.capitalize(), value=game)
    for game in GAME_MODS.keys()
])
@app_commands.choices(state=[
    app_commands.Choice(name="🟢起動中",value="active"),
    app_commands.Choice(name="🔴停止中",value="inactive")
])

async def set_server_state(interaction: discord.Interaction, game: str,mod: str=None,state:str="inactive"):
    await interaction.response.defer()

    value = state == "active"

    global SERVER_STATE

    if game not in GAME_MODS:
        await interaction.followup.send(f"サポートされていないゲーム: {game}")
        return

    if mod and mod not in GAME_MODS[game]:
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return

    mod_index = GAME_MODS[game].index(mod) if mod else 0
    mod_str = f" {mod}" if mod else ""

    if SERVER_STATE[game][mod_index] != value:  # 状態が変更される場合のみ更新
        SERVER_STATE[game][mod_index] = value
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} のstateを{'🟢 起動中' if value else '🔴 停止中'}に変更しました(実際に起動、停止していません)")
    else:
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} のstateは既に{'🟢 起動中' if value else '🔴 停止中'}です")

    if await check_all_server():
        await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
        await server_stop()
        await interaction.followup.send("VMを停止しました")

@set_server_state.autocomplete('mod')
async def mod_autocomplete(interaction: discord.Interaction, current: str):
    game = interaction.namespace.game
    if not game or game not in GAME_MODS:
        return []
    return [
        app_commands.Choice(name=mod, value=mod)
        for mod in GAME_MODS[game] if current.lower() in mod.lower()
    ]
#ここまでstate_setコマンド

@bot.event
async def on_ready():
    print(f'{bot.user} としてログインしました')
    global VM_State
    tmp_vmState = await check_vm_state()
    if tmp_vmState != -1:
        VM_State = tmp_vmState
    await bot.tree.sync()

# 各ゲームサーバーを起動,停止するシェルスクリプトを実行させる関数
async def execute_remote_script(is_start:bool,game: str, mod: str = None) -> str:

    try:
        async with asyncssh.connect(hostname, username=username,client_keys=[private_key_path],known_hosts=None) as conn:
            # gameとmodに基づいてスクリプトを選択
            script_name = ""
            return_text = ""
            if is_start:
                script_name = f"start_{game}"
                return_text = "起動"
            else:
                script_name = f"stop_{game}"
                return_text = "停止"

            if mod:
                script_name += f"_{mod}"
            script_name += ".sh"

            # スクリプトのパス（VMのパスに合わせて調整してください）
            script_path = f"/home/ripvv0105/Shell_Script/{game}/{script_name}"
            
            # スクリプトを実行         
            result = await conn.run(f"bash {script_path}")

            if result.exit_status == 0:
                return f"{game}{'(' + mod + ')' if mod else ''} サーバーが正常に{return_text}しました。\n{result.stdout}"
            else:
                return f"{game}{'(' + mod + ')' if mod else ''} サーバーの{return_text}中にエラーが発生しました。\n{result.stderr}"
    
    except asyncssh.Error as e:
        return f"SSH接続エラー: {str(e)}"

    except Exception as e:
        return f"予期せぬエラーが発生しました: {str(e)}"

# すべてのゲームサーバーが停止しているか確認し停止しているならVMを停止する関数
async def check_all_server() -> bool:
    global VM_State
    #すべてのゲームサーバーが停止しているかチェック
    all_servers_stopped = all(not any(states) for states in SERVER_STATE.values())
    print(f"{all_servers_stopped}:{VM_State}")
    if all_servers_stopped and VM_State:
        return True
    else:
        return False
    return False

# VMの起動や状態の確認を行う関数
async def setup_server() -> str:
    global VM_State
    vm_state = await check_vm_state()
    if vm_state == -1: 
        return 'VMの状態確認中にエラーが発生しました'

    if vm_state:
        VM_State = True
        return 'vmはすでに起動中です'
    else:
        await server_start()
        return 'vmを起動します。'

async def check_vm_state() -> typing.Union[bool,int]:
    try:
        output = await server_state()
        return "RUNNING" in output
    except Exception as e:
        return -1

# VM起動処理
async def server_start():
    command = f'/snap/bin/gcloud compute instances start {MINECRAFT_INSTANCE_NAME} --project {GCP_PROJECT_NAME} --zone {MINECRAFT_INSTANCE_ZONE}'
    process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout,stderr = await process.communicate()
    global VM_State
    VM_State = True
    if process.returncode != 0:
        print(f"サーバー起動中にエラーが発生:{stderr.decode()}")

# VM停止処理
async def server_stop():
    command = f'/snap/bin/gcloud compute instances stop {MINECRAFT_INSTANCE_NAME} --project {GCP_PROJECT_NAME} --zone {MINECRAFT_INSTANCE_ZONE}'
    process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout,stderr = await process.communicate()
    global VM_State
    VM_State = False
    if process.returncode != 0:
        print(f"サーバー起動中にエラーが発生:{stderr.decode()}")

# VMのステータス確認
async def server_state():
    command = "gcloud compute instances describe minecraft-server --project minecraft-server-422907 --zone asia-northeast1-b | grep 'status'"
    process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise Exception(f"サーバーの状態確認中にエラーが発生: {stderr.decode()}")
    return stdout.decode()

bot.run(TOKEN)
