import discord
from discord.ext import commands
from discord.ext.commands import has_permissions
from discord import app_commands
import sys
import asyncio
import asyncssh
import typing
import os # osモジュールをインポート
from dotenv import load_dotenv # dotenvモジュールからload_dotenvをインポート

# .envファイルから環境変数を読み込む
load_dotenv()

# --- 環境変数から設定を読み込む ---
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
SERVICE_ACCOUNT_ID = os.getenv('SERVICE_ACCOUNT_ID')
GCP_PROJECT_NAME = os.getenv('GCP_PROJECT_NAME')
MINECRAFT_INSTANCE_NAME = os.getenv('MINECRAFT_INSTANCE_NAME')
MINECRAFT_INSTANCE_ZONE = os.getenv('MINECRAFT_INSTANCE_ZONE')
VM_HOSTNAME = os.getenv('VM_HOSTNAME') # 変数名を変更 (hostname -> VM_HOSTNAME)
VM_USERNAME = os.getenv('VM_USERNAME') # 変数名を変更 (username -> VM_USERNAME)
PRIVATE_KEY_PATH = os.getenv('SSH_PRIVATE_KEY_PATH') # 変数名を変更 (private_key_path -> PRIVATE_KEY_PATH)

# --- 設定値の存在チェック (オプションだが推奨) ---
if not TOKEN:
    print("エラー: DISCORD_BOT_TOKENが.envファイルに設定されていません。")
    sys.exit(1)
if not VM_HOSTNAME or not VM_USERNAME or not PRIVATE_KEY_PATH:
    print("エラー: VM接続情報 (VM_HOSTNAME, VM_USERNAME, SSH_PRIVATE_KEY_PATH) のいずれかが.envファイルに設定されていません。")
    # SSH接続が必須でない場合は、ここでsys.exit(1) するか、適切に処理
    # sys.exit(1)
if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
    print("警告: GCP関連情報の一部が.envファイルに設定されていません。GCP連携機能が動作しない可能性があります。")


# DiscordのIntentsを設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ゲーム名とModのリスト サンプルです
GAME_MODS = {
    "minecraft": ["vanilla_1.21.5"],
    "terraria": ["vanilla_expart", "calamity_andmore"],
}
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

    mod_index = GAME_MODS[game].index(mod) if mod and mod in GAME_MODS[game] else 0 # mod 존재 및 유효성 검사 추가

    if mod and mod not in GAME_MODS[game]: # このチェックはmod_indexの前に来るべき
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return

    if SERVER_STATE[game][mod_index]:
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} サーバーは既に起動しています。")
        return

    true_count = sum(sum(states) for states in SERVER_STATE.values())
    if true_count == 3:
        await interaction.followup.send("サーバーを3つ開いているためこれ以上開けません")
        return

    mod_str = f" {mod}" if mod else ""
    # vmが起動しているかチェックと起動していなかったら起動
    result = await setup_server()
    if "起動します" in result: # VM起動処理が開始された場合
        await interaction.followup.send(f"{result} (VM起動には時間がかかる場合があります)") # ユーザーに進捗を伝える
        while await check_vm_state() == False:
            await asyncio.sleep(20)
        VM_State = True # VMが起動したことを反映
        await asyncio.sleep(20) # SSH接続可能になるまでの待機時間を追加
        await interaction.followup.send("VMが起動しました。サーバーを起動します...")
    elif "vmはすでに起動中です" in result:
        await interaction.followup.send(f"{result}")
        VM_State = True # VMが起動していることを反映
    else: # VMの状態確認エラーなど
        await interaction.followup.send(f"{result}")
        return


    if not VM_HOSTNAME or not VM_USERNAME or not PRIVATE_KEY_PATH:
        await interaction.followup.send("VM接続情報が設定されていないため、リモートスクリプトを実行できません。")
        return

    result = await execute_remote_script(True,game,mod)

    if "起動しました" in result:
        SERVER_STATE[game][mod_index] = True
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを起動しました。(立ち上げに数分かかることがあります)")
    else:
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーの起動に失敗しました。\n詳細: {result}") # エラー詳細も表示
        if await check_all_server():
            await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
            await server_stop()
            await interaction.followup.send("VMを停止しました")

@start_server.autocomplete('mod')
async def mod_autocomplete_start(interaction: discord.Interaction, current: str): # 関数名を変更
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

    mod_index = GAME_MODS[game].index(mod) if mod and mod in GAME_MODS[game] else 0 # mod存在及び有効性検査追加

    if mod and mod not in GAME_MODS[game]:
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return

    mod_str = f" {mod}" if mod else ""

    if not SERVER_STATE[game][mod_index]: # 状態チェックを最初に
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーは既に停止しています。")
        return

    if not VM_HOSTNAME or not VM_USERNAME or not PRIVATE_KEY_PATH:
        await interaction.followup.send("VM接続情報が設定されていないため、リモートスクリプトを実行できません。")
        return

    await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを停止します(数分かかることがあります)")
    result = await execute_remote_script(False,game,mod)

    if "停止しました" in result:
        SERVER_STATE[game][mod_index] = False
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーを停止しました。")
    else:
        await interaction.followup.send(f"{game.capitalize()}{mod_str} サーバーの停止に失敗しました。\n詳細: {result}") # エラー詳細も表示

    if await check_all_server():
        await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
        await server_stop()
        await interaction.followup.send("VMを停止しました")

@stop_server.autocomplete('mod')
async def mod_autocomplete_stop(interaction: discord.Interaction, current: str): # 関数名を変更
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
    vm_status_text = "確認中..."
    if VM_HOSTNAME and VM_USERNAME and PRIVATE_KEY_PATH: # GCP関連の情報もチェックに追加しても良い
        vm_running = await check_vm_state()
        if vm_running == -1:
            vm_status_text = "⚠️ VM状態確認エラー"
        elif vm_running:
            vm_status_text = "🟢 起動中"
            VM_State = True # check_vm_stateの結果をグローバル変数に反映
        else:
            vm_status_text = "🔴 停止中"
            VM_State = False # check_vm_stateの結果をグローバル変数に反映
    else:
        vm_status_text = "ℹ️ VM情報未設定"

    embed.add_field(name="VM状態", value=vm_status_text, inline=False)


    # サーバー状態
    for game, mods in GAME_MODS.items():
        game_status = []
        for i, mod_name_val in enumerate(mods): # mod -> mod_name_val に変更
            status = "🟢 起動中" if SERVER_STATE[game][i] else "🔴 停止中"
            # mod_name = mod_name_val # mod_name_val を使用
            mod_display_name = f"`{mod_name_val:<24}`"  # 左寄せで24文字分のスペースを確保
            game_status.append(f"{mod_display_name}: {status}")

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

    if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
        await interaction.followup.send("GCP情報が設定されていないため、VMを停止できません。ローカルの状態のみリセットしました。")
        return

    await server_stop() # server_stopは内部でVM_StateをFalseにする
    await interaction.followup.send("VMを強制的に停止しました (GCPへの指示)。ローカルの状態もリセットされました。")

#ここまでresetコマンド

#ここからstate_setコマンド
@bot.tree.command(name="state_set",description="(基本使用しないでください)stateを手動で変更する。(サーバーエラー落ち用)")
@app_commands.checks.has_permissions(administrator=True) # 管理者権限を追加
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
async def set_server_state(interaction: discord.Interaction, game: str, state:str, mod: str=None): # modとstateの順番を変更 (describeに合わせて)
    await interaction.response.defer()

    value = state == "active"

    global SERVER_STATE

    if game not in GAME_MODS:
        await interaction.followup.send(f"サポートされていないゲーム: {game}")
        return

    if mod and mod not in GAME_MODS[game]:
        await interaction.followup.send(f"{game}に対してサポートされていないMod: {mod}")
        return

    mod_index = GAME_MODS[game].index(mod) if mod and mod in GAME_MODS[game] else 0
    # mod_str = f" {mod}" if mod else "" # mod_strは使われていない

    if SERVER_STATE[game][mod_index] != value:  # 状態が変更される場合のみ更新
        SERVER_STATE[game][mod_index] = value
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} のstateを{'🟢 起動中' if value else '🔴 停止中'}に変更しました(実際のサーバー状態は変更していません)")
    else:
        await interaction.followup.send(f"{game.capitalize()}{' ' + mod if mod else ''} のstateは既に{'🟢 起動中' if value else '🔴 停止中'}です")

    # VM自動停止ロジックは、実際のサーバーの状態に基づいて行われるべきなので、
    # state_setコマンド実行後に自動でVMを停止するのは意図しない動作になる可能性があるためコメントアウト。
    # ユーザーが手動で状態を調整した後、必要であれば手動でVMを停止する運用を推奨。
    # if await check_all_server():
    #     await interaction.followup.send("サーバーがすべて停止しているためVMを停止します")
    #     await server_stop()
    #     await interaction.followup.send("VMを停止しました")

@set_server_state.autocomplete('mod')
async def mod_autocomplete_set_state(interaction: discord.Interaction, current: str): # 関数名を変更
    game = interaction.namespace.game
    if not game or game not in GAME_MODS:
        return []
    return [
        app_commands.Choice(name=mod_val, value=mod_val) # mod -> mod_val
        for mod_val in GAME_MODS[game] if current.lower() in mod_val.lower()
    ]
#ここまでstate_setコマンド

@bot.event
async def on_ready():
    print(f'{bot.user} としてログインしました')
    global VM_State
    # 起動時にVMの状態を確認し、VM_Stateを初期化
    if GCP_PROJECT_NAME and MINECRAFT_INSTANCE_NAME and MINECRAFT_INSTANCE_ZONE:
        initial_vm_state = await check_vm_state()
        if initial_vm_state == -1:
            print("起動時のVM状態確認に失敗しました。VM_StateはFalseのままです。")
            VM_State = False
        else:
            VM_State = initial_vm_state
            print(f"起動時のVM状態: {'起動中' if VM_State else '停止中'}")
    else:
        print("GCP情報が不足しているため、起動時のVM状態確認をスキップします。VM_StateはFalseです。")
        VM_State = False

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)}個のコマンドを同期しました。")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

# 各ゲームサーバーを起動,停止するシェルスクリプトを実行させる関数
async def execute_remote_script(is_start:bool, game: str, mod: str = None) -> str:
    if not VM_HOSTNAME or not VM_USERNAME or not PRIVATE_KEY_PATH:
        return "SSH接続情報が.envファイルに設定されていません。"

    try:
        async with asyncssh.connect(VM_HOSTNAME, username=VM_USERNAME, client_keys=[PRIVATE_KEY_PATH], known_hosts=None) as conn: # known_hosts=None はセキュリティリスクあり。本番環境ではホストキー検証を推奨
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
            # VM側のユーザー名も環境変数から取得できるようにすると、より柔軟性が増す
            vm_user_home = f"/home/{VM_USERNAME}" # 一般的なホームディレクトリ構造を仮定
            script_path = f"{vm_user_home}/Shell_Script/{game}/{script_name}" # 例: /home/your_vm_user/Shell_Script/minecraft/start_minecraft_vanilla.sh

            result = await conn.run(f"bash {script_path}", check=False) # check=Falseで終了コード非0でも例外を発生させない

            if result.exit_status == 0:
                return f"{game}{'(' + mod + ')' if mod else ''} サーバーが正常に{return_text}しました。\n標準出力:\n{result.stdout or '(なし)'}"
            else:
                return f"{game}{'(' + mod + ')' if mod else ''} サーバーの{return_text}中にエラーが発生しました (終了コード: {result.exit_status})。\n標準エラー:\n{result.stderr or '(なし)'}\n標準出力:\n{result.stdout or '(なし)'}"

    except asyncssh.misc.PermissionDenied as e:
        return f"SSH接続エラー: 認証に失敗しました。秘密鍵のパスや内容、VM側の設定を確認してください。詳細: {str(e)}"
    except asyncssh.misc.ConnectionLost as e:
        return f"SSH接続エラー: 接続が失われました。VMが起動しているか、ネットワーク設定を確認してください。詳細: {str(e)}"
    except ConnectionRefusedError:
        return f"SSH接続エラー: 接続が拒否されました。VMが起動しているか、SSHサービスがVMで実行されているか、ファイアウォール設定を確認してください。"
    except TimeoutError:
        return f"SSH接続エラー: 接続がタイムアウトしました。VMのIPアドレスやネットワーク状態を確認してください。"
    except asyncssh.Error as e:
        return f"SSH接続エラーが発生しました: {str(e)}"
    except Exception as e:
        return f"リモートスクリプト実行中に予期せぬエラーが発生しました: {str(e)}"

# すべてのゲームサーバーが停止しているか確認し停止しているならVMを停止する関数
async def check_all_server() -> bool:
    global VM_State
    all_servers_stopped = all(not any(states) for states in SERVER_STATE.values())
    # print(f"DEBUG: all_servers_stopped: {all_servers_stopped}, VM_State: {VM_State}") # デバッグ用
    if all_servers_stopped and VM_State:
        return True
    return False # else節は不要

# VMの起動や状態の確認を行う関数
async def setup_server() -> str:
    global VM_State
    if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
        return 'GCP情報が.envファイルに設定されていません。VM操作はできません。'

    vm_state_result = await check_vm_state()
    if vm_state_result == -1:
        return 'VMの状態確認中にエラーが発生しました。詳細はBotのログを確認してください。'

    if vm_state_result:
        VM_State = True
        return 'VMはすでに起動中です。'
    else:
        # VM_Stateはserver_start内でTrueになるので、ここでは変更しない
        await server_start() # server_start内でエラーが発生した場合はログに出力される
        return 'VMを起動します。起動完了まで数分お待ちください。'


async def check_vm_state() -> typing.Union[bool, int]:
    if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
        print("エラー: GCP情報が不足しているため、VM状態を確認できません。")
        return -1 # エラーを示す特別な値
    try:
        command = f"gcloud compute instances describe {MINECRAFT_INSTANCE_NAME} --project {GCP_PROJECT_NAME} --zone {MINECRAFT_INSTANCE_ZONE} --format='get(status)'"
        process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            # インスタンスが存在しない場合などのエラーもここに該当する可能性
            error_message = stderr.decode().strip()
            if "was not found" in error_message:
                print(f"VM状態確認エラー: インスタンス '{MINECRAFT_INSTANCE_NAME}' が見つかりません。 {error_message}")
                return False # 見つからない場合は停止中とみなすか、エラーとするか選択。ここでは停止中とする。
            else:
                print(f"VM状態確認エラー: {error_message}")
                return -1 # その他のgcloudエラー
        
        status = stdout.decode().strip()
        return status == "RUNNING"
    except FileNotFoundError:
        print("エラー: gcloudコマンドが見つかりません。gcloud SDKがインストールされ、PATHが通っているか確認してください。")
        return -1
    except Exception as e:
        print(f"VM状態確認中に予期せぬエラーが発生しました: {str(e)}")
        return -1

# VM起動処理
async def server_start():
    if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
        print("エラー: GCP情報が不足しているため、VMを起動できません。")
        return

    command = f'gcloud compute instances start {MINECRAFT_INSTANCE_NAME} --project {GCP_PROJECT_NAME} --zone {MINECRAFT_INSTANCE_ZONE}'
    process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout,stderr = await process.communicate()
    if process.returncode == 0:
        global VM_State
        VM_State = True
        print(f"VM '{MINECRAFT_INSTANCE_NAME}' の起動を指示しました。")
    else:
        print(f"VM '{MINECRAFT_INSTANCE_NAME}' の起動中にエラーが発生しました:\n{stderr.decode()}")
        # VM_State は変更しない（起動失敗の可能性があるため）

# VM停止処理
async def server_stop():
    if not GCP_PROJECT_NAME or not MINECRAFT_INSTANCE_NAME or not MINECRAFT_INSTANCE_ZONE:
        print("エラー: GCP情報が不足しているため、VMを停止できません。")
        return

    command = f'gcloud compute instances stop {MINECRAFT_INSTANCE_NAME} --project {GCP_PROJECT_NAME} --zone {MINECRAFT_INSTANCE_ZONE}'
    process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout,stderr = await process.communicate()
    if process.returncode == 0:
        global VM_State
        VM_State = False
        print(f"VM '{MINECRAFT_INSTANCE_NAME}' の停止を指示しました。")
    else:
        print(f"VM '{MINECRAFT_INSTANCE_NAME}' の停止中にエラーが発生しました:\n{stderr.decode()}")
        # VM_State は変更しない（停止失敗の可能性があるため）

# server_state() は check_vm_state() に統合されたため削除

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Discordボットトークンが設定されていません。スクリプトを終了します。")