/**
 * Ontyra memory settings page, browser half.
 *
 * It registers a section on the general Settings page and renders the shared `ConfigForm` for the
 * `ontyra` entry itself. Writes go through that form, which is the one place the Host validates and
 * fences them; the credential value never travels through the Cordis patch — it is written to the
 * credential store instead, and the config only keeps the reference name.
 *
 * Hand-written as a lazy-CJS factory (no build step): the module system materializes it the first
 * time the Settings page needs the section.
 */

window.__ModuleLoader__.load({
  id: 'dsh-ontyra-memory',
  factory: (require) => {
    const React = require('react')
    const module = { exports: {} }

    /** Dictionary namespace owned by this card. */
    const NS = 'settings.ontyra'
    /** Loader entry id of the Host half; also the settings namespace it registers. */
    const ENTRY = 'ontyra'
    /** Id of this card in the `settings.section` list: the package name. */
    const CARD = 'dsh-ontyra-memory'
    /** Id of the Memory tab in the `conversation.view` row. */
    const TAB = 'ontyra-memories'

    /** English copy. */
    const en = {
      title: 'Ontyra memory',
      description: 'A long-term memory daemon shared by every harness: a briefing at session start, recall on each prompt, and memory tools.',
      connection: 'Daemon',
      behaviour: 'What it does',
      baseUrl: 'Daemon URL',
      baseUrlHint: 'Where the Ontyra REST API listens. Loopback is expected.',
      tokenRef: 'Token reference',
      tokenRefHint: 'Name of the credential that holds the bearer token. The token itself is stored separately.',
      token: 'Bearer token',
      tokenHint: 'Stored in the credential store, never in the plugin patch. Leave empty to keep the current one.',
      tokenConfigured: 'A token is stored for this reference.',
      tokenMissing: 'No token stored; the daemon is treated as unauthenticated.',
      saveToken: 'Save token',
      clearToken: 'Clear token',
      harness: 'Harness name',
      harnessHint: 'Sent as X-Ontyra-Harness, so memories can tell which harness they came from.',
      briefing: 'Session briefing',
      briefingHint: 'Inject what applies at this location at the start of a session and after compaction.',
      recall: 'Recall per prompt',
      recallHint: 'Inject memories relevant to the first prompt of each step.',
      budgetTokens: 'Context budget',
      budgetTokensHint: 'Token budget for each rendered pack.',
      timeoutMs: 'Timeout (ms)',
      timeoutMsHint: 'How long one daemon call may take before the plugin gives up quietly.',
      toolTimeoutMs: 'Tool timeout (ms)',
      toolTimeoutMsHint: 'How long a tool call may take; a daemon on another host needs more than the default.',
      tools: 'Memory tools',
      toolsHint: 'Expose recall, check, briefing, ask, get and remember to the model.',
      toolsProfile: 'Tool profile',
      toolsProfileHint: 'lite keeps the working set; full adds the ontology tools.',
      profileLite: 'Lite — the working set',
      profileFull: 'Full — plus ontology',
      episodes: 'Record episodes',
      episodesHint: 'Send each prompt and answer to the daemon as a session episode.',
      capture: 'Ask to record lessons',
      captureHint: 'Remind the model to record what was settled: on rule-like prompts and every few prompts. No extra turn.',
      captureEvery: 'Capture every',
      captureEveryHint: 'Prompts between capture reminders.',
      subagents: 'Subagents too',
      subagentsHint: 'Inject and record inside subagent sessions as well.',
      saving: 'Saving…',
      reset: 'Reset',
      unavailable: 'This deployment does not offer the Ontyra settings entry.',
  loading: 'Reading the Ontyra settings…',
      readOnly: 'This deployment stores settings read-only.',
      failed: 'The deployment did not accept these values.',
      invalidNumber: 'Enter a whole number, or leave it empty for the default.',
      tab: "Memory",
      refresh: "Refresh",
      empty: "Nothing remembered yet.",
      loadFailed: "Could not load memories.",
      filter: "Kind",
      save: 'Save',
      saved: 'Saved.',
      edit: 'Edit',
      cancel: 'Cancel',
      forget: 'Forget',
      forgetConfirm: 'Forget it?',
      pending: 'Waiting for approval.',
      saveFailed: 'Could not save.',
      editReason: 'edited in the Memory tab',
      forgotReason: 'forgotten from the Memory tab',
      testConnection: 'Test connection',
      testing: 'Testing…',
      connected: 'Connected',
      unreachable: 'Not reachable',
      details: 'Details',
      hide: 'Hide',
      pendingWrites: 'write(s) not sent yet',
      retry: 'Send now',
    }

    /** Simplified Chinese copy, keyed against the English one. */
    const zh = {
      title: 'Ontyra 记忆',
      description: '所有 harness 共用的长期记忆守护进程：会话开始时给简报、每条 prompt 做召回，并提供记忆工具。',
      connection: '守护进程',
      behaviour: '行为',
      baseUrl: '守护进程地址',
      baseUrlHint: 'Ontyra REST API 监听的地址，通常是本机回环。',
      tokenRef: '令牌引用',
      tokenRefHint: '保存 bearer 令牌的凭据名。令牌本身单独存放。',
      token: 'Bearer 令牌',
      tokenHint: '存进凭据存储，不写进插件配置。留空表示保持当前值。',
      tokenConfigured: '该引用下已存有令牌。',
      tokenMissing: '尚未存令牌；守护进程按无鉴权对待。',
      saveToken: '保存令牌',
      clearToken: '清除令牌',
      harness: 'harness 名',
      harnessHint: '作为 X-Ontyra-Harness 发送，便于记忆区分来源。',
      briefing: '会话简报',
      briefingHint: '会话开始与上下文压缩后，注入当前地点适用的记忆。',
      recall: '逐条召回',
      recallHint: '在每个步骤的首条 prompt 上注入相关记忆。',
      budgetTokens: '上下文预算',
      budgetTokensHint: '每次渲染包的 token 预算。',
      timeoutMs: '超时（毫秒）',
      timeoutMsHint: '单次调用允许的时长，超时静默放弃。',
      toolTimeoutMs: '工具超时（毫秒）',
      toolTimeoutMsHint: '一次工具调用的上限；远端服务需要比默认更长的等待。',
      tools: '记忆工具',
      toolsHint: '向模型暴露 recall、check、briefing、ask、get 与 remember。',
      toolsProfile: '工具集',
      toolsProfileHint: 'lite 是常用集合；full 额外包含本体工具。',
      profileLite: 'Lite — 常用集合',
      profileFull: 'Full — 加上本体工具',
      episodes: '记录 episode',
      episodesHint: '把每条 prompt 与回答作为会话 episode 发给守护进程。',
      capture: '提醒记录经验',
      captureHint: '提醒模型记录已确定的内容：遇到规则类措辞时，以及每隔若干轮。不额外占用回合。',
      captureEvery: '提醒间隔',
      captureEveryHint: '两次提醒之间相隔的 prompt 数。',
      subagents: '包含子代理',
      subagentsHint: '子代理会话里也做注入与记录。',
      saving: '保存中…',
      reset: '恢复默认',
      unavailable: '本部署没有提供 Ontyra 设置项。',
  loading: '正在读取 Ontyra 设置…',
      readOnly: '本部署的设置为只读。',
      failed: '本部署没有接受这些值。',
      invalidNumber: '请填整数；留空表示使用默认值。',
      tab: "记忆",
      refresh: "刷新",
      empty: "还没有任何记忆。",
      loadFailed: "无法加载记忆。",
      filter: "类型",
      save: '保存',
      saved: '已保存。',
      edit: '编辑',
      cancel: '取消',
      forget: '遗忘',
      forgetConfirm: '确定遗忘？',
      pending: '等待批准。',
      saveFailed: '无法保存。',
      editReason: '在记忆标签页中编辑',
      forgotReason: '从记忆标签页遗忘',
      testConnection: '测试连接',
      testing: '正在测试…',
      connected: '已连接',
      unreachable: '无法连接',
      details: '详情',
      hide: '收起',
      pendingWrites: '条写入尚未发出',
      retry: '立即发送',
    }

    /** Japanese copy, keyed against the English one. */
    const ja = {
      title: 'Ontyra メモリ',
      description: 'すべてのハーネスが共有する長期記憶デーモン。セッション開始時のブリーフィング、プロンプトごとのリコール、記憶ツールを提供します。',
      connection: 'デーモン',
      behaviour: '動作',
      baseUrl: 'デーモンの URL',
      baseUrlHint: 'Ontyra REST API が待ち受けるアドレス。通常はループバックです。',
      tokenRef: 'トークン参照',
      tokenRefHint: 'ベアラートークンを保管する資格情報の名前。トークン本体は別に保存されます。',
      token: 'ベアラートークン',
      tokenHint: '資格情報ストアに保存され、プラグイン設定には書き込まれません。空欄なら現在の値を保持します。',
      tokenConfigured: 'この参照にはトークンが保存されています。',
      tokenMissing: 'トークン未保存。デーモンは認証なしとして扱われます。',
      saveToken: 'トークンを保存',
      clearToken: 'トークンを消去',
      harness: 'ハーネス名',
      harnessHint: 'X-Ontyra-Harness として送信され、記憶の出所を区別できます。',
      briefing: 'セッション・ブリーフィング',
      briefingHint: 'セッション開始時とコンパクション後に、現在地に該当する記憶を注入します。',
      recall: 'プロンプトごとのリコール',
      recallHint: '各ステップの最初のプロンプトに関連する記憶を注入します。',
      budgetTokens: 'コンテキスト予算',
      budgetTokensHint: 'レンダリングされたパックあたりのトークン予算。',
      timeoutMs: 'タイムアウト（ミリ秒）',
      timeoutMsHint: '1 回のデーモン呼び出しの上限。超えると静かに諦めます。',
      toolTimeoutMs: 'ツールのタイムアウト（ミリ秒）',
      toolTimeoutMsHint: 'ツール呼び出しの上限。別ホストのデーモンには既定より長い時間が要ります。',
      tools: '記憶ツール',
      toolsHint: 'recall、check、briefing、ask、get、remember をモデルに公開します。',
      toolsProfile: 'ツールセット',
      toolsProfileHint: 'lite は常用セット、full はオントロジーツールも含みます。',
      profileLite: 'Lite — 常用セット',
      profileFull: 'Full — オントロジー込み',
      episodes: 'エピソードを記録',
      episodesHint: '各プロンプトと回答をセッションのエピソードとしてデーモンに送ります。',
      capture: '学びの記録を促す',
      captureHint: '決まったことを記録するようモデルに促します。ルール的な発言のとき、および数プロンプトごと。追加のターンは使いません。',
      captureEvery: '促す間隔',
      captureEveryHint: '促しの間に挟むプロンプト数。',
      subagents: 'サブエージェントも含める',
      subagentsHint: 'サブエージェントのセッションでも注入と記録を行います。',
      saving: '保存中…',
      reset: 'リセット',
      unavailable: 'このデプロイは Ontyra 設定項目を提供していません。',
      loading: 'Ontyra 設定を読み込んでいます…',
      readOnly: 'このデプロイの設定は読み取り専用です。',
      failed: 'このデプロイは値を受け付けませんでした。',
      invalidNumber: '整数を入力するか、空欄のままにしてください。',
      tab: "メモリ",
      refresh: "更新",
      empty: "まだ記憶はありません。",
      loadFailed: "記憶を読み込めませんでした。",
      filter: "種類",
      save: '保存',
      saved: '保存しました。',
      edit: '編集',
      cancel: 'キャンセル',
      forget: '忘れる',
      forgetConfirm: '忘れますか？',
      pending: '承認待ちです。',
      saveFailed: '保存できませんでした。',
      editReason: 'メモリタブで編集',
      forgotReason: 'メモリタブから忘れる',
      testConnection: '接続をテスト',
      testing: 'テスト中…',
      connected: '接続済み',
      unreachable: '接続できません',
      details: '詳細',
      hide: '閉じる',
      pendingWrites: '件の書き込みが未送信',
      retry: '今すぐ送信',
    }

    /** Spanish copy, keyed against the English one. */
    const es = {
      title: 'Memoria Ontyra',
      description: 'Un demonio de memoria a largo plazo compartido por todos los harness: informe al iniciar la sesión, recuerdo en cada prompt y herramientas de memoria.',
      connection: 'Demonio',
      behaviour: 'Qué hace',
      baseUrl: 'URL del demonio',
      baseUrlHint: 'Dónde escucha la API REST de Ontyra. Se espera loopback.',
      tokenRef: 'Referencia del token',
      tokenRefHint: 'Nombre de la credencial que guarda el token bearer. El token se almacena aparte.',
      token: 'Token bearer',
      tokenHint: 'Se guarda en el almacén de credenciales, nunca en el parche del plugin. Déjalo vacío para mantener el actual.',
      tokenConfigured: 'Hay un token guardado para esta referencia.',
      tokenMissing: 'No hay token guardado; el demonio se trata como sin autenticación.',
      saveToken: 'Guardar token',
      clearToken: 'Borrar token',
      harness: 'Nombre del harness',
      harnessHint: 'Se envía como X-Ontyra-Harness, para que las memorias sepan de qué harness vienen.',
      briefing: 'Informe de sesión',
      briefingHint: 'Inyecta lo que aplica en este lugar al iniciar la sesión y tras la compactación.',
      recall: 'Recuerdo por prompt',
      recallHint: 'Inyecta memorias relevantes en el primer prompt de cada paso.',
      budgetTokens: 'Presupuesto de contexto',
      budgetTokensHint: 'Presupuesto de tokens por paquete renderizado.',
      timeoutMs: 'Tiempo límite (ms)',
      timeoutMsHint: 'Cuánto puede tardar una llamada al demonio antes de rendirse en silencio.',
      toolTimeoutMs: 'Tiempo límite de herramientas (ms)',
      toolTimeoutMsHint: 'Cuánto puede tardar una llamada de herramienta; un demonio en otro host necesita más.',
      tools: 'Herramientas de memoria',
      toolsHint: 'Expone recall, check, briefing, ask, get y remember al modelo.',
      toolsProfile: 'Conjunto de herramientas',
      toolsProfileHint: 'lite mantiene el conjunto habitual; full añade las herramientas de ontología.',
      profileLite: 'Lite — el conjunto habitual',
      profileFull: 'Full — más ontología',
      episodes: 'Registrar episodios',
      episodesHint: 'Envía cada prompt y respuesta al demonio como episodio de sesión.',
      capture: 'Pedir registrar lecciones',
      captureHint: 'Recuerda al modelo anotar lo acordado: ante frases tipo regla y cada pocos prompts. Sin turno extra.',
      captureEvery: 'Cada cuántos',
      captureEveryHint: 'Prompts entre recordatorios de captura.',
      subagents: 'También subagentes',
      subagentsHint: 'Inyecta y registra también en sesiones de subagentes.',
      saving: 'Guardando…',
      reset: 'Restablecer',
      unavailable: 'Este despliegue no ofrece la entrada de ajustes de Ontyra.',
      loading: 'Leyendo los ajustes de Ontyra…',
      readOnly: 'Este despliegue guarda los ajustes como solo lectura.',
      failed: 'El despliegue no aceptó estos valores.',
      invalidNumber: 'Introduce un número entero o déjalo vacío para usar el valor por defecto.',
      tab: "Memoria",
      refresh: "Actualizar",
      empty: "Todavía no hay recuerdos.",
      loadFailed: "No se pudieron cargar los recuerdos.",
      filter: "Tipo",
      save: 'Guardar',
      saved: 'Guardado.',
      edit: 'Editar',
      cancel: 'Cancelar',
      forget: 'Olvidar',
      forgetConfirm: '¿Olvidarlo?',
      pending: 'Esperando aprobación.',
      saveFailed: 'No se pudo guardar.',
      editReason: 'editado en la pestaña Memoria',
      forgotReason: 'olvidado desde la pestaña Memoria',
      testConnection: 'Probar conexión',
      testing: 'Probando…',
      connected: 'Conectado',
      unreachable: 'No disponible',
      details: 'Detalles',
      hide: 'Ocultar',
      pendingWrites: 'escritura(s) sin enviar',
      retry: 'Enviar ahora',
    }

    /** French copy, keyed against the English one. */
    const fr = {
      title: 'Mémoire Ontyra',
      description: "Un démon de mémoire à long terme partagé par tous les harness : un briefing au début de la session, un rappel à chaque prompt et des outils de mémoire.",
      connection: 'Démon',
      behaviour: 'Ce qu’il fait',
      baseUrl: 'URL du démon',
      baseUrlHint: "Où l'API REST d'Ontyra écoute. La boucle locale est attendue.",
      tokenRef: 'Référence du jeton',
      tokenRefHint: 'Nom de l’identifiant qui contient le jeton bearer. Le jeton est stocké séparément.',
      token: 'Jeton bearer',
      tokenHint: 'Stocké dans le magasin d’identifiants, jamais dans le patch du plugin. Laissez vide pour conserver l’actuel.',
      tokenConfigured: 'Un jeton est stocké pour cette référence.',
      tokenMissing: 'Aucun jeton stocké ; le démon est traité comme non authentifié.',
      saveToken: 'Enregistrer le jeton',
      clearToken: 'Effacer le jeton',
      harness: 'Nom du harness',
      harnessHint: 'Envoyé comme X-Ontyra-Harness, pour que les mémoires sachent d’où elles viennent.',
      briefing: 'Briefing de session',
      briefingHint: 'Injecte ce qui s’applique ici au début d’une session et après une compaction.',
      recall: 'Rappel par prompt',
      recallHint: 'Injecte les mémoires pertinentes au premier prompt de chaque étape.',
      budgetTokens: 'Budget de contexte',
      budgetTokensHint: 'Budget de jetons par paquet rendu.',
      timeoutMs: 'Délai (ms)',
      timeoutMsHint: 'Durée maximale d’un appel au démon avant d’abandonner discrètement.',
      toolTimeoutMs: 'Délai des outils (ms)',
      toolTimeoutMsHint: 'Durée maximale d’un appel d’outil ; un démon sur un autre hôte demande davantage.',
      tools: 'Outils de mémoire',
      toolsHint: 'Expose recall, check, briefing, ask, get et remember au modèle.',
      toolsProfile: 'Jeu d’outils',
      toolsProfileHint: 'lite garde l’ensemble courant ; full ajoute les outils d’ontologie.',
      profileLite: 'Lite — l’ensemble courant',
      profileFull: 'Full — plus l’ontologie',
      episodes: 'Enregistrer les épisodes',
      episodesHint: 'Envoie chaque prompt et réponse au démon comme épisode de session.',
      capture: 'Demander à noter les leçons',
      captureHint: 'Rappelle au modèle de noter ce qui est acté : sur une formulation de règle et tous les quelques prompts. Sans tour supplémentaire.',
      captureEvery: 'Toutes les',
      captureEveryHint: 'Prompts entre deux rappels de capture.',
      subagents: 'Sous-agents aussi',
      subagentsHint: 'Injecte et enregistre aussi dans les sessions de sous-agents.',
      saving: 'Enregistrement…',
      reset: 'Réinitialiser',
      unavailable: 'Ce déploiement ne propose pas l’entrée de réglages Ontyra.',
      loading: 'Lecture des réglages Ontyra…',
      readOnly: 'Ce déploiement stocke les réglages en lecture seule.',
      failed: 'Le déploiement n’a pas accepté ces valeurs.',
      invalidNumber: 'Saisissez un entier, ou laissez vide pour la valeur par défaut.',
      tab: "Mémoire",
      refresh: "Actualiser",
      empty: "Aucun souvenir pour l'instant.",
      loadFailed: "Impossible de charger les souvenirs.",
      filter: "Type",
      save: 'Enregistrer',
      saved: 'Enregistré.',
      edit: 'Modifier',
      cancel: 'Annuler',
      forget: 'Oublier',
      forgetConfirm: 'Oublier ?',
      pending: 'En attente d’approbation.',
      saveFailed: 'Impossible d’enregistrer.',
      editReason: 'modifié dans l’onglet Mémoire',
      forgotReason: 'oublié depuis l’onglet Mémoire',
      testConnection: 'Tester la connexion',
      testing: 'Test en cours…',
      connected: 'Connecté',
      unreachable: 'Injoignable',
      details: 'Détails',
      hide: 'Masquer',
      pendingWrites: 'écriture(s) non envoyée(s)',
      retry: 'Envoyer maintenant',
    }

    /** German copy, keyed against the English one. */
    const de = {
      title: 'Ontyra-Gedächtnis',
      description: 'Ein Langzeitgedächtnis-Daemon, den alle Harnesses teilen: Briefing zu Sitzungsbeginn, Recall bei jedem Prompt und Gedächtniswerkzeuge.',
      connection: 'Daemon',
      behaviour: 'Was er tut',
      baseUrl: 'Daemon-URL',
      baseUrlHint: 'Wo die Ontyra-REST-API lauscht. Loopback ist zu erwarten.',
      tokenRef: 'Token-Referenz',
      tokenRefHint: 'Name des Zugangsdaten-Eintrags, der das Bearer-Token hält. Das Token selbst liegt getrennt.',
      token: 'Bearer-Token',
      tokenHint: 'Wird im Zugangsdaten-Speicher abgelegt, nie im Plugin-Patch. Leer lassen, um das aktuelle zu behalten.',
      tokenConfigured: 'Für diese Referenz ist ein Token gespeichert.',
      tokenMissing: 'Kein Token gespeichert; der Daemon gilt als nicht authentifiziert.',
      saveToken: 'Token speichern',
      clearToken: 'Token löschen',
      harness: 'Harness-Name',
      harnessHint: 'Wird als X-Ontyra-Harness gesendet, damit Erinnerungen ihre Herkunft kennen.',
      briefing: 'Sitzungs-Briefing',
      briefingHint: 'Fügt zu Sitzungsbeginn und nach der Kompaktierung ein, was hier gilt.',
      recall: 'Recall je Prompt',
      recallHint: 'Fügt relevante Erinnerungen beim ersten Prompt jedes Schritts ein.',
      budgetTokens: 'Kontextbudget',
      budgetTokensHint: 'Token-Budget je gerendertem Paket.',
      timeoutMs: 'Zeitlimit (ms)',
      timeoutMsHint: 'Wie lange ein Daemon-Aufruf dauern darf, bevor das Plugin still aufgibt.',
      toolTimeoutMs: 'Werkzeug-Zeitlimit (ms)',
      toolTimeoutMsHint: 'Wie lange ein Werkzeugaufruf dauern darf; ein Daemon auf einem anderen Host braucht mehr.',
      tools: 'Gedächtniswerkzeuge',
      toolsHint: 'Stellt recall, check, briefing, ask, get und remember für das Modell bereit.',
      toolsProfile: 'Werkzeugsatz',
      toolsProfileHint: 'lite hält den Arbeitssatz; full ergänzt die Ontologie-Werkzeuge.',
      profileLite: 'Lite — der Arbeitssatz',
      profileFull: 'Full — plus Ontologie',
      episodes: 'Episoden aufzeichnen',
      episodesHint: 'Sendet jeden Prompt und jede Antwort als Sitzungsepisode an den Daemon.',
      capture: 'Zum Festhalten von Lehren auffordern',
      captureHint: 'Erinnert das Modell, Festgelegtes aufzuschreiben: bei regelartigen Prompts und alle paar Prompts. Kein zusätzlicher Zug.',
      captureEvery: 'Alle wie viele',
      captureEveryHint: 'Prompts zwischen zwei Capture-Erinnerungen.',
      subagents: 'Auch Subagenten',
      subagentsHint: 'Fügt auch in Subagenten-Sitzungen ein und zeichnet dort auf.',
      saving: 'Speichern…',
      reset: 'Zurücksetzen',
      unavailable: 'Diese Installation bietet keinen Ontyra-Einstellungseintrag.',
      loading: 'Ontyra-Einstellungen werden gelesen…',
      readOnly: 'Diese Installation speichert Einstellungen schreibgeschützt.',
      failed: 'Die Installation hat diese Werte nicht angenommen.',
      invalidNumber: 'Ganze Zahl eingeben oder leer lassen für den Standardwert.',
      tab: "Gedächtnis",
      refresh: "Aktualisieren",
      empty: "Noch nichts gespeichert.",
      loadFailed: "Erinnerungen konnten nicht geladen werden.",
      filter: "Art",
      save: 'Speichern',
      saved: 'Gespeichert.',
      edit: 'Bearbeiten',
      cancel: 'Abbrechen',
      forget: 'Vergessen',
      forgetConfirm: 'Wirklich vergessen?',
      pending: 'Wartet auf Freigabe.',
      saveFailed: 'Konnte nicht speichern.',
      editReason: 'im Speicher-Tab bearbeitet',
      forgotReason: 'aus dem Speicher-Tab vergessen',
      testConnection: 'Verbindung testen',
      testing: 'Wird getestet…',
      connected: 'Verbunden',
      unreachable: 'Nicht erreichbar',
      details: 'Details',
      hide: 'Ausblenden',
      pendingWrites: 'Schreibvorgänge nicht gesendet',
      retry: 'Jetzt senden',
    }

    /** Russian copy, keyed against the English one. */
    const ru = {
      title: 'Память Ontyra',
      description: 'Демон долговременной памяти, общий для всех harness: брифинг в начале сессии, recall на каждый запрос и инструменты памяти.',
      connection: 'Демон',
      behaviour: 'Что делает',
      baseUrl: 'URL демона',
      baseUrlHint: 'Где слушает REST API Ontyra. Обычно это loopback.',
      tokenRef: 'Ссылка на токен',
      tokenRefHint: 'Имя учётных данных с bearer-токеном. Сам токен хранится отдельно.',
      token: 'Bearer-токен',
      tokenHint: 'Сохраняется в хранилище учётных данных, а не в патче плагина. Оставьте пустым, чтобы сохранить текущий.',
      tokenConfigured: 'Для этой ссылки токен сохранён.',
      tokenMissing: 'Токен не сохранён; демон считается без аутентификации.',
      saveToken: 'Сохранить токен',
      clearToken: 'Удалить токен',
      harness: 'Имя harness',
      harnessHint: 'Отправляется как X-Ontyra-Harness, чтобы память знала источник.',
      briefing: 'Брифинг сессии',
      briefingHint: 'Вставляет то, что применимо здесь, в начале сессии и после компакции.',
      recall: 'Recall на запрос',
      recallHint: 'Вставляет релевантные воспоминания в первый запрос каждого шага.',
      budgetTokens: 'Бюджет контекста',
      budgetTokensHint: 'Бюджет токенов на каждый собранный пакет.',
      timeoutMs: 'Таймаут (мс)',
      timeoutMsHint: 'Сколько может длиться вызов демона, прежде чем плагин тихо откажется.',
      toolTimeoutMs: 'Таймаут инструментов (мс)',
      toolTimeoutMsHint: 'Сколько может длиться вызов инструмента; демону на другом хосте нужно больше.',
      tools: 'Инструменты памяти',
      toolsHint: 'Даёт модели recall, check, briefing, ask, get и remember.',
      toolsProfile: 'Набор инструментов',
      toolsProfileHint: 'lite — рабочий набор; full добавляет инструменты онтологии.',
      profileLite: 'Lite — рабочий набор',
      profileFull: 'Full — плюс онтология',
      episodes: 'Записывать эпизоды',
      episodesHint: 'Отправляет каждый запрос и ответ демону как эпизод сессии.',
      capture: 'Просить фиксировать выводы',
      captureHint: 'Напоминает модели записать решённое: при формулировках-правилах и раз в несколько запросов. Без лишнего хода.',
      captureEvery: 'Как часто',
      captureEveryHint: 'Сколько запросов между напоминаниями.',
      subagents: 'И субагенты',
      subagentsHint: 'Вставлять и записывать также в сессиях субагентов.',
      saving: 'Сохранение…',
      reset: 'Сбросить',
      unavailable: 'В этом развёртывании нет раздела настроек Ontyra.',
      loading: 'Чтение настроек Ontyra…',
      readOnly: 'В этом развёртывании настройки только для чтения.',
      failed: 'Развёртывание не приняло эти значения.',
      invalidNumber: 'Введите целое число или оставьте пусто для значения по умолчанию.',
      tab: "Память",
      refresh: "Обновить",
      empty: "Пока ничего не запомнено.",
      loadFailed: "Не удалось загрузить воспоминания.",
      filter: "Тип",
      save: 'Сохранить',
      saved: 'Сохранено.',
      edit: 'Изменить',
      cancel: 'Отмена',
      forget: 'Забыть',
      forgetConfirm: 'Забыть?',
      pending: 'Ожидает одобрения.',
      saveFailed: 'Не удалось сохранить.',
      editReason: 'изменено на вкладке памяти',
      forgotReason: 'забыто на вкладке памяти',
      testConnection: 'Проверить подключение',
      testing: 'Проверка…',
      connected: 'Подключено',
      unreachable: 'Недоступно',
      details: 'Подробности',
      hide: 'Скрыть',
      pendingWrites: 'записей не отправлено',
      retry: 'Отправить сейчас',
    }

    /** Portuguese copy, keyed against the English one. */
    const pt = {
      title: 'Memória Ontyra',
      description: 'Um daemon de memória de longo prazo partilhado por todos os harness: briefing no início da sessão, recall em cada prompt e ferramentas de memória.',
      connection: 'Daemon',
      behaviour: 'O que faz',
      baseUrl: 'URL do daemon',
      baseUrlHint: 'Onde a API REST do Ontyra escuta. Espera-se loopback.',
      tokenRef: 'Referência do token',
      tokenRefHint: 'Nome da credencial que guarda o token bearer. O token em si fica separado.',
      token: 'Token bearer',
      tokenHint: 'Guardado no armazenamento de credenciais, nunca no patch do plugin. Deixe vazio para manter o atual.',
      tokenConfigured: 'Existe um token guardado para esta referência.',
      tokenMissing: 'Sem token guardado; o daemon é tratado como sem autenticação.',
      saveToken: 'Guardar token',
      clearToken: 'Limpar token',
      harness: 'Nome do harness',
      harnessHint: 'Enviado como X-Ontyra-Harness, para as memórias saberem de onde vieram.',
      briefing: 'Briefing da sessão',
      briefingHint: 'Injeta o que se aplica aqui no início da sessão e após a compactação.',
      recall: 'Recall por prompt',
      recallHint: 'Injeta memórias relevantes no primeiro prompt de cada passo.',
      budgetTokens: 'Orçamento de contexto',
      budgetTokensHint: 'Orçamento de tokens por pacote renderizado.',
      timeoutMs: 'Tempo limite (ms)',
      timeoutMsHint: 'Quanto pode durar uma chamada ao daemon antes de o plugin desistir em silêncio.',
      toolTimeoutMs: 'Tempo limite das ferramentas (ms)',
      toolTimeoutMsHint: 'Quanto pode durar uma chamada de ferramenta; um daemon noutro host precisa de mais.',
      tools: 'Ferramentas de memória',
      toolsHint: 'Expõe recall, check, briefing, ask, get e remember ao modelo.',
      toolsProfile: 'Conjunto de ferramentas',
      toolsProfileHint: 'lite mantém o conjunto habitual; full acrescenta as ferramentas de ontologia.',
      profileLite: 'Lite — o conjunto habitual',
      profileFull: 'Full — mais ontologia',
      episodes: 'Registar episódios',
      episodesHint: 'Envia cada prompt e resposta ao daemon como episódio de sessão.',
      capture: 'Pedir para registar lições',
      captureHint: 'Lembra o modelo de registar o que ficou decidido: em frases tipo regra e a cada poucos prompts. Sem turno extra.',
      captureEvery: 'A cada quantos',
      captureEveryHint: 'Prompts entre lembretes de captura.',
      subagents: 'Também subagentes',
      subagentsHint: 'Injeta e registra também em sessões de subagentes.',
      saving: 'A guardar…',
      reset: 'Repor',
      unavailable: 'Esta instalação não oferece a entrada de definições do Ontyra.',
      loading: 'A ler as definições do Ontyra…',
      readOnly: 'Esta instalação guarda as definições só para leitura.',
      failed: 'A instalação não aceitou estes valores.',
      invalidNumber: 'Introduza um número inteiro ou deixe vazio para o valor predefinido.',
      tab: "Memória",
      refresh: "Atualizar",
      empty: "Nada memorizado ainda.",
      loadFailed: "Não foi possível carregar as memórias.",
      filter: "Tipo",
      save: 'Salvar',
      saved: 'Salvo.',
      edit: 'Editar',
      cancel: 'Cancelar',
      forget: 'Esquecer',
      forgetConfirm: 'Esquecer mesmo?',
      pending: 'Aguardando aprovação.',
      saveFailed: 'Não foi possível salvar.',
      editReason: 'editado na aba Memória',
      forgotReason: 'esquecido da aba Memória',
      testConnection: 'Testar conexão',
      testing: 'Testando…',
      connected: 'Conectado',
      unreachable: 'Inacessível',
      details: 'Detalhes',
      hide: 'Ocultar',
      pendingWrites: 'gravação(ões) não enviada(s)',
      retry: 'Enviar agora',
    }

    /** Every dictionary this card registers. */
    const DICTS = { en, zh, ja, es, fr, de, ru, pt }

    /**
     * One editable setting, in card order.
     * `kind` decides the control and how a draft string becomes a value.
     */
    const FIELDS = [
      { key: 'baseUrl', kind: 'text', group: 'connection', label: 'baseUrl', hint: 'baseUrlHint' },
      { key: 'tokenRef', kind: 'text', group: 'connection', label: 'tokenRef', hint: 'tokenRefHint' },
      { key: 'harness', kind: 'text', group: 'connection', label: 'harness', hint: 'harnessHint' },
      { key: 'briefing', kind: 'boolean', group: 'behaviour', label: 'briefing', hint: 'briefingHint' },
      { key: 'recall', kind: 'boolean', group: 'behaviour', label: 'recall', hint: 'recallHint' },
      { key: 'budgetTokens', kind: 'number', group: 'behaviour', label: 'budgetTokens', hint: 'budgetTokensHint' },
      { key: 'timeoutMs', kind: 'number', group: 'behaviour', label: 'timeoutMs', hint: 'timeoutMsHint' },
      { key: 'toolTimeoutMs', kind: 'number', group: 'behaviour', label: 'toolTimeoutMs', hint: 'toolTimeoutMsHint' },
      { key: 'tools', kind: 'boolean', group: 'behaviour', label: 'tools', hint: 'toolsHint' },
      { key: 'toolsProfile', kind: 'select', group: 'behaviour', label: 'toolsProfile', hint: 'toolsProfileHint', choices: ['lite', 'full'] },
      { key: 'episodes', kind: 'boolean', group: 'behaviour', label: 'episodes', hint: 'episodesHint' },
      { key: 'capture', kind: 'boolean', group: 'behaviour', label: 'capture', hint: 'captureHint' },
      { key: 'captureEvery', kind: 'number', group: 'behaviour', label: 'captureEvery', hint: 'captureEveryHint' },
      { key: 'subagents', kind: 'boolean', group: 'behaviour', label: 'subagents', hint: 'subagentsHint' },
    ]

    /** Token shown by the form; the stored value is never returned by the Host. */
    const TOKEN_KEY = 'token'

    const styles = {
      root: { display: 'flex', flexDirection: 'column', gap: '18px', padding: '4px 0', fontSize: '13px' },
      lead: { margin: 0, opacity: 0.75, lineHeight: 1.5 },
      group: { display: 'flex', flexDirection: 'column', gap: '12px' },
      legend: { margin: 0, fontSize: '12px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', opacity: 0.6 },
      field: { display: 'flex', flexDirection: 'column', gap: '4px' },
      row: { display: 'flex', alignItems: 'center', gap: '8px' },
      label: { fontWeight: 600 },
      hint: { margin: 0, opacity: 0.6, fontSize: '12px', lineHeight: 1.45 },
      input: {
        font: 'inherit',
        padding: '6px 8px',
        borderRadius: '6px',
        border: '1px solid var(--dsw-alias-border-default, rgba(128,128,128,0.35))',
        background: 'var(--dsw-alias-surface-raised, transparent)',
        color: 'var(--dsw-alias-text-primary, inherit)',
        maxWidth: '420px',
      },
      actions: { display: 'flex', alignItems: 'center', gap: '10px' },
      button: {
        font: 'inherit',
        padding: '5px 12px',
        borderRadius: '6px',
        border: '1px solid var(--dsw-alias-border-default, rgba(128,128,128,0.35))',
        background: 'var(--dsw-alias-surface-raised, transparent)',
        color: 'var(--dsw-alias-text-primary, inherit)',
        cursor: 'pointer',
      },
      quiet: { font: 'inherit', padding: '5px 8px', border: 'none', background: 'transparent', color: 'inherit', opacity: 0.7, cursor: 'pointer' },
      note: { margin: 0, fontSize: '12px', opacity: 0.75 },
      error: { margin: 0, fontSize: '12px', color: 'var(--dsw-alias-text-danger, #d9534f)' },
    }

    /**
     * Coerce one draft into the value the Host should store.
     * @param {string} kind - `text`, `number`, `boolean` or `select`.
     * @param {unknown} draft - what the control produced.
     * @param {readonly string[]} [choices] - the values a `select` may hold.
     * @returns {{ ok: true, value: unknown } | { ok: false }} the value, or a rejected draft.
     */
    function coerce(kind, draft, choices) {
      if (kind === 'boolean') return { ok: true, value: draft === true }
      if (kind === 'select') return choices.includes(draft) ? { ok: true, value: draft } : { ok: false }
      const text = String(draft ?? '').trim()
      if (kind === 'number') {
        if (text === '') return { ok: true, value: undefined }
        const value = Number(text)
        return Number.isInteger(value) && value >= 0 ? { ok: true, value } : { ok: false }
      }
      if (text === '') return { ok: true, value: undefined }
      return { ok: true, value: text }
    }

    /**
     * Build the card component for one client context.
     * @param {any} ctx - the browser plugin context.
     * @param {(key: string) => string} t - this card's locale reader.
     * @returns {() => any} the React component to register.
     */
    function makeCard(ctx, t) {
      /**
       * Show, rather than swallow, a render failure: the settings panel has no error surface of its
       * own for a section, so an uncaught throw would leave an empty column and no clue.
       */
      class CardBoundary extends React.Component {
        constructor(props) {
          super(props)
          this.state = { error: null }
        }

        /** @param {any} error - the render failure. */
        static getDerivedStateFromError(error) {
          return { error }
        }

        /** @returns {any} the section, or the failure text. */
        render() {
          const error = this.state.error
          if (error === null) return this.props.children
          console.error('[ontyra] the settings section failed to render', error)
          return React.createElement(
            'p',
            { style: styles.error },
            `Ontyra settings failed to render: ${error && error.message ? error.message : String(error)}`,
          )
        }
      }

      /** @returns {any} the shared form for the plugin's entry; writes go through it. */
      function formOf() {
        return ctx.configForms.get(ENTRY)
      }

      /**
       * Read this entry's section from the describe mirror instead of the form snapshot.
       *
       * The form derives `ready` only after rehydrating and validating the Host's serialized
       * schema; when that fails it stays `loading` forever and the section renders empty. The
       * mirror carries the same redacted JSON without that gate, so the card renders from it and
       * only uses the form to write.
       * @returns {{ status: string, value: any, writable: boolean, error: string, served: string[] }} the section.
       */
      function readSection() {
        const mirror = ctx.configForms.describe()
        const snapshot = mirror.getSnapshot()
        const view = snapshot.view
        if (view === undefined) {
          return { status: 'loading', value: {}, writable: false, error: snapshot.error ?? '', served: [] }
        }
        const section = view.namespaces.find((candidate) => candidate.ns === ENTRY)
        if (section === undefined) {
          return {
            status: 'unavailable',
            value: {},
            writable: view.writable === true,
            error: '',
            served: view.namespaces.map((candidate) => candidate.ns),
          }
        }
        return {
          status: 'ready',
          value: section.value ?? {},
          writable: view.writable === true,
          error: '',
          served: [],
        }
      }

      function OntyraCard() {
        const form = React.useMemo(() => formOf(), [ctx])
        const [section, setSection] = React.useState(readSection)
        // Subscribe rather than read in place: the snapshot object is rebuilt on every call, so a
        // render that reads it directly re-renders forever.
        React.useEffect(() => {
          const mirror = ctx.configForms.describe()
          // The mirror reads the Host only on demand; without this the card could wait for another
          // settings surface to trigger the first read and sit in `loading` forever.
          if (typeof mirror.ensure === 'function') void mirror.ensure()
          setSection(readSection())
          return mirror.subscribe(() => { setSection(readSection()) })
        }, [ctx])
        const [drafts, setDrafts] = React.useState({})
        const [status, setStatus] = React.useState('')
        const [tokenDraft, setTokenDraft] = React.useState('')
        const [tokenState, setTokenState] = React.useState(/** @type {any} */ (undefined))
        /**
         * The last connection probe: `null` before anyone asked, `{ testing: true }` while asking,
         * otherwise what the Host half saw. Nothing is inferred from a saved base URL — a daemon can
         * be configured and unreachable.
         * @type {[any, (next: any) => void]}
         */
        const [probe, setProbe] = React.useState(/** @type {any} */ (null))

        const value = section.value ?? {}
        const tokenRef = String(drafts.tokenRef ?? value.tokenRef ?? 'ONTYRA_TOKEN')
        const writable = section.writable === true

        React.useEffect(() => {
          let alive = true
          const credentials = ctx.remote?.credentials
          if (!credentials || typeof credentials.describe !== 'function') return () => { alive = false }
          Promise.resolve(credentials.describe(tokenRef))
            .then((described) => { if (alive) setTokenState(described) })
            .catch(() => { if (alive) setTokenState(undefined) })
          return () => { alive = false }
        }, [ctx, tokenRef])

        try {
          return renderBody()
        } catch (error) {
          console.error('[ontyra] the settings section failed to render', error)
          return React.createElement('p', { style: styles.error }, `Ontyra settings failed to render: ${error && error.message ? error.message : String(error)}`)
        }

        /** Build the section the panel renders; separated so a failure is visible, not silent. */
        function renderBody() {
        if (section.status !== 'ready') {
          const key = section.status === 'unavailable' ? 'unavailable'
            : section.status === 'loading' ? 'loading' : 'readOnly'
          const detail = section.status === 'unavailable' && section.served.length > 0
            ? ` (served: ${section.served.join(', ')})`
            : section.error === '' ? '' : ` (${section.error})`
          return React.createElement('p', { style: styles.lead }, `${t(key)}${detail}`)
        }

        /** Show a draft for one field, falling back to the stored value. */
        function shown(field) {
          const draft = drafts[field.key]
          if (draft !== undefined) return draft
          const current = value[field.key]
          if (field.kind === 'boolean') return current === true
          return current === undefined || current === null ? '' : String(current)
        }

        /** Stage one edit; booleans take effect on Save like every other field. */
        function edit(key, next) {
          setStatus('')
          setDrafts((previous) => ({ ...previous, [key]: next }))
        }

        /** Write every staged value the Host will accept. */
        async function save() {
          if (!writable) return
          setStatus('saving')
          const entries = Object.entries(drafts)
          if (entries.length === 0) {
            setStatus('saved')
            return
          }
          for (const [key, draft] of entries) {
            const field = FIELDS.find((candidate) => candidate.key === key)
            if (!field) continue
            const coerced = coerce(field.kind, draft, field.choices)
            if (coerced.ok === false) {
              setStatus('invalidNumber')
              return
            }
            const accepted = coerced.value === undefined
              ? await form.unset(key)
              : await form.set(key, coerced.value)
            if (accepted !== true) {
              setStatus('failed')
              return
            }
          }
          setDrafts({})
          setStatus('saved')
        }

        /** Write the token into the credential store under the reference the config names. */
        async function saveToken() {
          const credentials = ctx.remote?.credentials
          if (!credentials || typeof credentials.set !== 'function' || tokenDraft === '') return
          setStatus('saving')
          try {
            await credentials.set(tokenRef, tokenDraft)
            setTokenDraft('')
            setTokenState(await credentials.describe(tokenRef))
            setStatus('saved')
          } catch {
            setStatus('failed')
          }
        }

        /** Drop the stored token, leaving the environment fallback alone. */
        async function clearToken() {
          const credentials = ctx.remote?.credentials
          if (!credentials || typeof credentials.unset !== 'function') return
          setStatus('saving')
          try {
            await credentials.unset(tokenRef)
            setTokenState(await credentials.describe(tokenRef))
            setStatus('saved')
          } catch {
            setStatus('failed')
          }
        }

        /** Ask the Host half to try the daemon, and show what it saw. */
        async function check() {
          setProbe({ testing: true })
          const result = await testConnection(t)
          setProbe(result)
        }

        /** One labelled control. */
        function control(field) {
          const id = `ontyra-setting-${field.key}`
          const disabled = !writable
          const hint = React.createElement('p', { style: styles.hint }, t(field.hint))
          if (field.kind === 'boolean') {
            const input = React.createElement('input', {
              id,
              type: 'checkbox',
              checked: shown(field) === true,
              disabled,
              onChange: (event) => { edit(field.key, event.target.checked) },
            })
            return React.createElement(
              'div',
              { style: styles.field, key: field.key },
              React.createElement('div', { style: styles.row }, input, React.createElement('label', { htmlFor: id, style: styles.label }, t(field.label))),
              hint,
            )
          }
          if (field.kind === 'select') {
            const select = React.createElement(
              'select',
              {
                id,
                value: String(shown(field)),
                disabled,
                style: styles.input,
                onChange: (event) => { edit(field.key, event.target.value) },
              },
              ...field.choices.map((choice) => React.createElement(
                'option',
                { key: choice, value: choice },
                t(choice === 'full' ? 'profileFull' : 'profileLite'),
              )),
            )
            return React.createElement(
              'div',
              { style: styles.field, key: field.key },
              React.createElement('label', { htmlFor: id, style: styles.label }, t(field.label)),
              select,
              hint,
            )
          }
          const input = React.createElement('input', {
            id,
            type: field.kind === 'number' ? 'number' : 'text',
            value: shown(field),
            disabled,
            style: styles.input,
            onChange: (event) => { edit(field.key, event.target.value) },
          })
          return React.createElement(
            'div',
            { style: styles.field, key: field.key },
            React.createElement('label', { htmlFor: id, style: styles.label }, t(field.label)),
            input,
            hint,
          )
        }

        const groups = [
          { id: 'connection', legend: t('connection'), fields: FIELDS.filter((field) => field.group === 'connection') },
          { id: 'behaviour', legend: t('behaviour'), fields: FIELDS.filter((field) => field.group === 'behaviour') },
        ]

        const tokenNote = tokenState === undefined
          ? ''
          : tokenState.configured === true ? t('tokenConfigured') : t('tokenMissing')
        const tokenConfigured = tokenState !== undefined && tokenState.configured === true

        const message = status === 'saved' ? t('saved')
          : status === 'failed' ? t('failed')
            : status === 'invalidNumber' ? t('invalidNumber')
              : ''

        /**
         * The connection line: saved settings say what should be reached, this says what was.
         * @returns {any} one paragraph plus a button.
         */
        function statusLine() {
          const text = probe === null ? ''
            : probe.testing === true ? t('testing')
              : probe.ok === true ? `${t('connected')}${probe.version === '' ? '' : ` — ${probe.version}`}`
                : `${t('unreachable')}: ${probe.error}`
          return React.createElement(
            'div',
            { style: styles.field },
            React.createElement(
              'div',
              { style: styles.row },
              React.createElement('button', { type: 'button', style: styles.quiet, onClick: check, disabled: probe !== null && probe.testing === true }, t('testConnection')),
              text === '' ? null : React.createElement('span', { style: probe?.ok === true ? styles.note : styles.error }, text),
            ),
          )
        }

        return React.createElement(
          'div',
          { style: styles.root },
          React.createElement('p', { style: styles.lead }, t('description')),
          statusLine(),
          ...groups.map((group) => React.createElement(
            'div',
            { style: styles.group, key: group.id },
            React.createElement('p', { style: styles.legend }, group.legend),
            ...group.fields.map(control),
            group.id === 'connection' ? React.createElement(
              'div',
              { style: styles.field },
              React.createElement('label', { htmlFor: 'ontyra-setting-token', style: styles.label }, t('token')),
              React.createElement('input', {
                id: 'ontyra-setting-token',
                type: 'password',
                value: tokenDraft,
                autoComplete: 'off',
                style: styles.input,
                onChange: (event) => { setTokenDraft(event.target.value) },
              }),
              React.createElement('p', { style: styles.hint }, t('tokenHint')),
              React.createElement(
                'div',
                { style: styles.actions },
                React.createElement('button', { type: 'button', style: styles.button, onClick: saveToken, disabled: tokenDraft === '' }, t('saveToken')),
                tokenConfigured ? React.createElement('button', { type: 'button', style: styles.quiet, onClick: clearToken }, t('clearToken')) : null,
              ),
              tokenNote === '' ? null : React.createElement('p', { style: styles.note }, tokenNote),
            ) : null,
          )),
          React.createElement(
            'div',
            { style: styles.actions },
            React.createElement('button', { type: 'button', style: styles.button, onClick: save, disabled: !writable || status === 'saving' }, t(status === 'saving' ? 'saving' : 'save')),
            Object.keys(drafts).length === 0 ? null : React.createElement('button', { type: 'button', style: styles.quiet, onClick: () => { setDrafts({}); setStatus('') } }, t('reset')),
            message === '' ? null : React.createElement('p', { style: status === 'saved' ? styles.note : styles.error }, message),
          ),
        )
        }
      }

      /** The registered section: the card behind a boundary, so a failure shows as text. */
      function OntyraSection() {
        return React.createElement(CardBoundary, null, React.createElement(OntyraCard))
      }

      return OntyraSection
    }

    /** Required client services. */
    const inject = ['slots', 'locale', 'configForms', 'remote', 'remote.credentials']

    /**
     * Register the section on the general Settings page while the Host serves the namespace.
     * @param {any} ctx - the browser plugin context.
     */
    function apply(ctx) {
      ctx.effect(() => ctx.locale.register(NS, DICTS), 'ontyra: dictionaries')
      const bound = ctx.locale.bind(NS)
      // A missing string must never render as an empty line: fall back to the key itself.
      const t = (key) => {
        const text = bound(key)
        return typeof text === 'string' && text !== '' ? text : key
      }
      const Card = makeCard(ctx, t)
      ctx.effect(() => ctx.configForms.whileServed([ENTRY], () => ctx.slots.inject('settings.section', () => ctx.slots.register({
        name: 'settings.section', id: CARD, order: 45, label: () => t('title'), locale: NS,
      }, Card))), 'ontyra: settings section')
      // The Memory tab sits beside Chat and Trajectory: the library, read-only for now, with the
      // same data the tools see. It waits for the conversation UI to offer the slot.
      const Panel = makePanel(t)
      ctx.effect(() => ctx.slots.inject('conversation.view', () => ctx.slots.register({
        name: 'conversation.view', id: TAB, order: 31, label: () => t('tab'), locale: NS,
      }, Panel)), 'ontyra: memories view')
    }

    /**
     * Read the library through this plugin's own route.
     *
     * The Host half is the only side that knows the daemon URL and holds its token, so the tab
     * asks `/ontyra/memories` and gets a small, already-shaped payload back.
     * @param {(key: string) => string} t - translator for the failure copy.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ status: string, items: any[], error: string }>} what the panel renders.
     */
    async function fetchMemories(t, request = fetch) {
      try {
        const response = await request('/ontyra/memories?limit=200', { headers: { 'x-ontyra-client': 'tab' } })
        const body = await response.json()
        if (body?.ok === true) return { status: 'ready', items: body.items ?? [], error: '' }
        return { status: 'error', items: [], error: body?.error ?? t('loadFailed') }
      } catch (error) {
        return { status: 'error', items: [], error: String(error?.message ?? error) }
      }
    }

    /**
     * Ask the Host half to correct one memory.
     *
     * `fields` overlays the current revision, so only the text is sent: a correction through the
     * tab goes through the same proposal path as the `ontyra_correct` tool, and comes back
     * `pending` under a review policy like any other write.
     * @param {(key: string) => string} t - translator for the failure copy.
     * @param {{ memory: string, text: string, reason?: string }} change - what to correct.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ ok: boolean, error: string, pending: boolean }>} whether it landed.
     */
    async function correctMemory(t, { memory, text, reason }, request = fetch) {
      try {
        const response = await request('/ontyra/correct', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-ontyra-client': 'tab' },
          body: JSON.stringify({ memory, fields: { text }, reason: reason ?? t('editReason') }),
        })
        const body = await response.json()
        if (body?.ok === true) return { ok: true, error: '', pending: body.result?.status === 'pending' }
        return { ok: false, error: body?.error ?? t('saveFailed'), pending: false }
      } catch (error) {
        return { ok: false, error: String(error?.message ?? error), pending: false }
      }
    }

    /**
     * Ask the Host half to retire one memory, or everything under a uri.
     * @param {(key: string) => string} t - translator for the copy.
     * @param {{ memory?: string, uri?: string }} target - exactly one of the two.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ ok: boolean, error: string, pending: boolean }>} whether it landed.
     */
    async function forgetMemory(t, target, request = fetch) {
      try {
        const response = await request('/ontyra/forget', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-ontyra-client': 'tab' },
          body: JSON.stringify({ ...target, reason: t('forgotReason') }),
        })
        const body = await response.json()
        if (body?.ok === true) return { ok: true, error: '', pending: body.result?.status === 'pending' }
        return { ok: false, error: body?.error ?? t('saveFailed'), pending: false }
      } catch (error) {
        return { ok: false, error: String(error?.message ?? error), pending: false }
      }
    }

    /**
     * Ask the Host half whether the daemon answers right now.
     *
     * The card cannot call the daemon itself (no token, different origin), so "connected" is
     * something the Host half reports after an actual request, not something the card infers.
     * @param {(key: string) => string} t - translator for the failure copy.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ ok: boolean, version: string, error: string }>} the probe result.
     */
    async function testConnection(t, request = fetch) {
      try {
        const response = await request('/ontyra/ping', { headers: { 'x-ontyra-client': 'tab' } })
        const body = await response.json()
        const daemon = body?.daemon
        if (daemon?.ok === true) return { ok: true, version: String(daemon.version ?? ''), error: '' }
        return { ok: false, version: '', error: daemon?.error ?? t('unreachable') }
      } catch (error) {
        return { ok: false, version: '', error: String(error?.message ?? error) }
      }
    }

    /**
     * What the queue still holds: writes the daemon never accepted.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ count: number, items: any[] }>} the pending writes.
     */
    async function fetchOutbox(request = fetch) {
      try {
        const response = await request('/ontyra/outbox', { headers: { 'x-ontyra-client': 'tab' } })
        const body = await response.json()
        return { count: Number(body?.count ?? 0), items: Array.isArray(body?.items) ? body.items : [] }
      } catch {
        return { count: 0, items: [] }
      }
    }

    /**
     * Ask the Host half to replay the queue now, rather than on the next tool call.
     * @param {(key: string) => string} t - translator for the failure copy.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ ok: boolean, sent: number, left: number, error: string }>} what happened.
     */
    async function retryOutbox(t, request = fetch) {
      try {
        const response = await request('/ontyra/outbox/retry', {
          method: 'POST',
          headers: { 'x-ontyra-client': 'tab' },
        })
        const body = await response.json()
        return {
          ok: body?.ok === true,
          sent: Number(body?.sent ?? 0),
          left: Number(body?.left ?? 0),
          error: body?.error ?? (body?.ok === true ? '' : t('unreachable')),
        }
      } catch (error) {
        return { ok: false, sent: 0, left: 0, error: String(error?.message ?? error) }
      }
    }

    /**
     * One memory with what it rests on, for the row someone expanded.
     * @param {string} id - the memory id.
     * @param {typeof fetch} [request] - the transport, injectable for tests.
     * @returns {Promise<{ ok: boolean, memory: any, error: string }>} the detail.
     */
    async function fetchDetail(id, request = fetch) {
      try {
        const response = await request(`/ontyra/memories/${encodeURIComponent(id)}`, { headers: { 'x-ontyra-client': 'tab' } })
        const body = await response.json()
        if (body?.ok === true) return { ok: true, memory: body.memory, error: '' }
        return { ok: false, memory: null, error: body?.error ?? 'could not load the detail' }
      } catch (error) {
        return { ok: false, memory: null, error: String(error?.message ?? error) }
      }
    }

    /**
     * A memory's detail as text a person can read without a schema.
     *
     * Bounded on purpose: the row is a peek, not an export, and an unbounded history would make
     * the conversation panel scroll for pages.
     * @param {any} memory - the detail response body.
     * @returns {string} printable text.
     */
    function printable(memory) {
      if (memory === null || memory === undefined) return ''
      try {
        const text = typeof memory === 'string' ? memory : JSON.stringify(memory, null, 1)
        return text.length > 4000 ? `${text.slice(0, 4000)}\n…` : text
      } catch {
        return String(memory)
      }
    }

    /**
     * The Memory tab: the library the daemon holds, read through the Host half.
     *
     * The browser never sees the daemon URL's token: it asks this plugin's own route, which runs
     * beside the credential store. Reading the library does not touch the delivery ledger, so a
     * look around cannot make recall think a memory was already handed to the agent.
     * @param {(key: string) => string} t - translator for the panel's own strings.
     * @returns {Function} the React component the tab renders.
     */
    function makePanel(t) {
      const style = {
        page: { display: 'flex', flexDirection: 'column', gap: '10px', height: '100%', padding: '16px', overflow: 'auto', boxSizing: 'border-box' },
        bar: { display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' },
        heading: { margin: 0, fontSize: '15px', fontWeight: 600 },
        count: { fontSize: '12px', opacity: 0.6 },
        grow: { flex: 1 },
        row: { border: '1px solid var(--dsw-alias-border, #30363d)', borderRadius: '8px', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '6px' },
        rowHead: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', opacity: 0.75 },
        kind: { textTransform: 'uppercase', letterSpacing: '0.04em' },
        text: { fontSize: '13px', lineHeight: 1.45, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
        meta: { fontSize: '11px', opacity: 0.55, overflowWrap: 'anywhere' },
        notice: { fontSize: '13px', opacity: 0.7 },
        error: { fontSize: '13px', color: 'var(--dsw-alias-danger, #d1242f)' },
        action: { padding: '2px 8px', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', background: 'transparent', color: 'inherit', border: '1px solid var(--dsw-alias-border, #30363d)' },
        input: { width: '100%', minHeight: '64px', padding: '6px 8px', borderRadius: '6px', font: 'inherit', fontSize: '13px', background: 'transparent', color: 'inherit', border: '1px solid var(--dsw-alias-border, #30363d)', boxSizing: 'border-box' },
        detail: { margin: 0, maxHeight: '260px', overflow: 'auto', padding: '8px', borderRadius: '6px', fontSize: '11px', lineHeight: 1.4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: 'var(--dsw-alias-surface-sunken, rgba(127, 127, 127, 0.08))' },
        banner: { display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 10px', borderRadius: '6px', fontSize: '12px', border: '1px solid var(--dsw-alias-border, #30363d)' },
      }

      /** One memory row, with its edit, forget and detail affordances. */
      function Row({ item, editing, confirming, detail, busy, onEdit, onConfirm, onDraft, onSave, onCancel, onForget, onDetail }) {
        const anchors = Array.isArray(item.anchors) ? item.anchors : []
        const isEditing = editing !== null && editing.id === item.id
        const shown = detail === undefined ? null : detail
        return React.createElement('div', { style: style.row },
          React.createElement('div', { style: style.rowHead },
            React.createElement('span', { style: style.kind }, item.kind ?? ''),
            React.createElement('span', null, item.status ?? ''),
            item.scope ? React.createElement('span', null, item.scope) : null,
            React.createElement('span', { style: style.grow }),
            isEditing
              ? null
              : React.createElement('button', {
                type: 'button',
                style: style.action,
                disabled: busy,
                onClick: () => onDetail(item, shown === null),
              }, shown === null ? t('details') : t('hide')),
            isEditing
              ? null
              : React.createElement('button', { type: 'button', style: style.action, disabled: busy, onClick: () => onEdit(item) }, t('edit')),
            isEditing
              ? null
              : React.createElement('button', {
                type: 'button',
                style: style.action,
                disabled: busy,
                onClick: () => (confirming === item.id ? onForget(item) : onConfirm(item.id)),
              }, confirming === item.id ? t('forgetConfirm') : t('forget'))),
          isEditing
            ? React.createElement('div', { style: style.meta },
              React.createElement('textarea', {
                style: style.input,
                value: editing.text,
                onChange: (event) => onDraft(event.target.value),
              }),
              React.createElement('div', { style: style.rowHead },
                React.createElement('button', { type: 'button', style: style.action, disabled: busy, onClick: onSave }, t('save')),
                React.createElement('button', { type: 'button', style: style.action, disabled: busy, onClick: onCancel }, t('cancel'))))
            : React.createElement('div', { style: style.text }, item.text ?? ''),
          anchors.length > 0
            ? React.createElement('div', { style: style.meta }, anchors.map((anchor) => `${anchor.uri} (${anchor.match})`).join(' · '))
            : null,
          shown === null
            ? null
            : shown.status === 'loading'
              ? React.createElement('div', { style: style.meta }, t('loading'))
              : shown.status === 'error'
                ? React.createElement('div', { style: style.error }, shown.error)
                : React.createElement('pre', { style: style.detail }, shown.text))
      }

      return function Panel() {
        const [state, setState] = React.useState({ status: 'loading', items: [], error: '', kind: '' })
        const [editing, setEditing] = React.useState(null)
        const [confirming, setConfirming] = React.useState('')
        const [flash, setFlash] = React.useState('')
        const [busy, setBusy] = React.useState(false)
        /** @type {[Record<string, any>, (next: any) => void]} expanded row details, keyed by id */
        const [details, setDetails] = React.useState(/** @type {any} */ ({}))
        /** @type {[{ count: number, items: any[] }, (next: any) => void]} writes still waiting */
        const [outbox, setOutbox] = React.useState({ count: 0, items: [] })

        const load = React.useCallback(() => {
          setState((current) => ({ ...current, status: 'loading', error: '' }))
          fetchMemories(t).then((next) => setState({ ...next, kind: '' }))
          // A write that never reached the daemon is invisible to `GET /v1/memories`, so the queue
          // is read alongside the library: otherwise the tab would look complete while work waits.
          fetchOutbox().then(setOutbox)
        }, [])

        React.useEffect(() => { load() }, [load])

        const retry = React.useCallback(() => {
          setBusy(true)
          retryOutbox(t).then((result) => {
            setBusy(false)
            setFlash(result.ok ? `${t('saved')} (${result.sent})` : result.error)
            load()
          })
        }, [load])

        const detail = React.useCallback((item, open) => {
          if (!open) {
            setDetails((current) => {
              const next = { ...current }
              delete next[item.id]
              return next
            })
            return
          }
          setDetails((current) => ({ ...current, [item.id]: { status: 'loading', text: '', error: '' } }))
          fetchDetail(item.id).then((result) => {
            setDetails((current) => ({
              ...current,
              [item.id]: result.ok
                ? { status: 'ready', text: printable(result.memory), error: '' }
                : { status: 'error', text: '', error: result.error },
            }))
          })
        }, [])

        const announce = React.useCallback((result) => {
          setFlash(result.ok ? (result.pending ? t('pending') : t('saved')) : result.error)
          if (result.ok) load()
        }, [load])

        const save = React.useCallback(() => {
          if (editing === null) return
          setBusy(true)
          correctMemory(t, { memory: editing.id, text: editing.text }).then((result) => {
            setBusy(false)
            setEditing(null)
            announce(result)
          })
        }, [editing, announce])

        const forget = React.useCallback((item) => {
          setBusy(true)
          forgetMemory(t, { memory: item.id }).then((result) => {
            setBusy(false)
            setConfirming('')
            announce(result)
          })
        }, [announce])

        const kinds = React.useMemo(
          () => Array.from(new Set(state.items.map((item) => item?.kind).filter((kind) => typeof kind === 'string'))).sort(),
          [state.items],
        )
        const shown = state.kind === '' ? state.items : state.items.filter((item) => item.kind === state.kind)

        return React.createElement('div', { style: style.page },
          React.createElement('div', { style: style.bar },
            React.createElement('h3', { style: style.heading }, t('title')),
            React.createElement('span', { style: style.count }, `${shown.length}`),
            React.createElement('span', { style: style.grow }),
            kinds.length > 1
              ? React.createElement('select', {
                'aria-label': t('filter'),
                value: state.kind,
                style: { padding: '4px 6px', borderRadius: '6px', background: 'transparent', color: 'inherit', border: '1px solid var(--dsw-alias-border, #30363d)' },
                onChange: (event) => setState((current) => ({ ...current, kind: event.target.value })),
              },
              React.createElement('option', { value: '' }, t('filter')),
              ...kinds.map((kind) => React.createElement('option', { key: kind, value: kind }, kind)))
              : null,
            React.createElement('button', {
              type: 'button',
              onClick: load,
              style: { padding: '4px 10px', borderRadius: '6px', cursor: 'pointer', background: 'transparent', color: 'inherit', border: '1px solid var(--dsw-alias-border, #30363d)' },
            }, t('refresh'))),
          outbox.count > 0
            ? React.createElement('div', { style: style.banner },
              React.createElement('span', null, `${outbox.count} ${t('pendingWrites')}`),
              React.createElement('span', { style: style.grow }),
              React.createElement('button', { type: 'button', style: style.action, disabled: busy, onClick: retry }, t('retry')))
            : null,
          state.status === 'loading' ? React.createElement('div', { style: style.notice }, t('loading')) : null,
          flash !== '' ? React.createElement('div', { style: style.notice }, flash) : null,
          state.status === 'error' ? React.createElement('div', { style: style.error }, state.error) : null,
          state.status === 'ready' && shown.length === 0 ? React.createElement('div', { style: style.notice }, t('empty')) : null,
          ...shown.map((item) => React.createElement(Row, {
            key: item.id,
            item,
            editing,
            confirming,
            detail: details[item.id],
            busy,
            onDetail: detail,
            onEdit: (target) => { setConfirming(''); setEditing({ id: target.id, text: target.text ?? '' }) },
            onConfirm: (id) => { setEditing(null); setConfirming(id) },
            onDraft: (text) => setEditing((current) => (current === null ? current : { ...current, text })),
            onSave: save,
            onCancel: () => setEditing(null),
            onForget: forget,
          })))
      }
    }

    module.exports = { apply, correctMemory, fetchDetail, fetchMemories, fetchOutbox, forgetMemory, inject, name: 'dsh-ontyra-memory', retryOutbox, testConnection, dictionaries: DICTS }
    return module.exports
  },
})
