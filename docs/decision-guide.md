<!-- Owner's source document (Portuguese), kept verbatim. Decisions taken after v1.0 are recorded in docs/risk-assessment.md §7 and in the issues/PRs. -->

# Apple Music MCP — Guia de decisões para o Claude Code

**Versão:** 1.0 · **Data:** 2026-11-05 · **Dono do projeto:** Eike
**Status do código:** escrito inteiramente pelo Claude Code, **nunca executado contra a API real**.

---

## 0. Como usar este documento

- Leia o documento inteiro antes de qualquer alteração de código.
- As decisões aqui são do Eike. Se você discordar de alguma por motivo técnico, **levante a objeção antes de implementar**, com o motivo e a alternativa. Não contorne a decisão em silêncio.
- Itens marcados **[VERIFICAR]** são suposições que precisam ser confirmadas em documentação oficial ou no código antes de virar implementação. Não invente nomes de funções, endpoints ou limites.
- Itens marcados **[GATE]** exigem aprovação explícita do Eike antes de seguir.
- Itens marcados **[PADRÃO]** são valores iniciais definidos para não travar o trabalho; devem ser configuráveis e podem ser ajustados pelo Eike.
- Comunicação com o Eike: em português, direta, sem enrolação. Ele prefere uma objeção útil a uma concordância educada.

---

## 1. Visão e escopo

Um servidor MCP pessoal que permite ao Eike criar e manter playlists no Apple Music conversando com o Claude, de forma similar ao conector de Spotify que já existe no Claude (o Apple Music não tem um conector oficial).

**Isto é um MVP.** A prioridade é o sistema fazer de forma confiável o que se propõe. Melhorias de arquitetura (nuvem, multiusuário hospedado, API oficial) ficam para depois.

**Forma de uso:**
- O servidor roda **100% no Mac do Eike**, como servidor MCP local do Claude Desktop.
- Acesso pelo celular via **Claude Dispatch** (o app do celular manda a tarefa, o Claude Desktop no Mac executa). Requisitos: Mac acordado e app do Claude aberto.
- O Mac fica em casa, na tomada. Em viagens, o Mac vai junto.

**Usuários:** só o Eike. Mas o projeto deve funcionar para qualquer pessoa que clonar o repositório e rodar no próprio Mac com as próprias credenciais (um primo do Eike pode testar). Isso significa: zero dados pessoais no código, toda configuração por usuário fora do Git.

---

## 2. Restrições inegociáveis

1. **Custo zero.** Nenhum serviço pago. Nenhuma chamada a APIs pagas (Claude API, OpenAI, etc.). Tarefas agendadas são **código determinístico**, sem LLM. O julgamento com IA acontece quando o Eike conversa pelo Claude/Dispatch.
2. **Roda no Mac.** Sem Docker (ver §4), sem nuvem.
3. **Nenhum segredo no GitHub.** Nem em repositório privado, nem em GitHub Secrets. O CI roda sem credenciais.
4. **Segurança da conta Apple em primeiro lugar.** O Eike não aceita risco relevante de bloqueio da conta. Na dúvida, o comportamento mais conservador vence (ver Fase 0).
5. **Sem RAG.** Os dados são estruturados (reproduções, skips, curtidas, BPM); SQL resolve melhor e mais barato.

---

## 3. Fase 0 — Auditoria, riscos e validação [GATE]

**Nenhuma feature nova antes desta fase ser concluída e aprovada pelo Eike.**

### 3.1 Auditoria do código
- Leia todo o código e produza `docs/audit.md` com: o que está implementado, o que está incompleto, o que provavelmente não funciona, dívidas técnicas, e o que diverge deste guia.
- Explique ao Eike, em linguagem simples, o que exatamente fazem `apple-music-mcp login` e `apple-music-mcp doctor --write` (incluindo o que o `--write` cria/altera na conta e se limpa depois).

### 3.2 Avaliação de risco da API não oficial
Produza `docs/risk-assessment.md` com:
- Como o código autentica (de onde vem o developer token, como o user token é obtido, quais cabeçalhos e endpoints usa).
- **Pesquisa na internet [VERIFICAR, com fontes citadas]:**
  - O que os termos de uso da Apple (Apple Media Services Terms e afins) dizem sobre acesso automatizado ao music.apple.com.
  - Relatos de contas bloqueadas ou advertidas por ferramentas semelhantes (projetos open source que usam a API do player web).
  - Quanto tempo dura o user token e como a Apple trata uso fora do navegador.
- Classificação do risco (baixo/médio/alto) com justificativa, separando: risco de quebra técnica vs. risco à conta.
- Mitigações concretas (volume, padrão de requisições, cabeçalhos, frequência dos jobs agendados).
- Alternativas, com prós, contras e custo:
  - API oficial (MusicKit / Apple Music API com Apple Developer Program, US$ 99/ano) [VERIFICAR preço atual].
  - Abordagem local via AppleScript/app Música para o que for possível (criar playlist, adicionar faixas da biblioteca) [VERIFICAR limites, em especial adicionar faixas do catálogo que não estão na biblioteca].
  - Abordagem híbrida.
- Recomendação final: seguir, seguir com mitigações, ou trocar de abordagem.

**Se o risco à conta for médio ou alto, pare e apresente as alternativas ao Eike antes de qualquer outra coisa.**

### 3.3 Validação real guiada
- Conduza o Eike, passo a passo, a rodar `login` e `doctor --write` no Mac dele.
- Corrija o que quebrar. Registre o resultado em `docs/audit.md`.
- Critério de saída: todas as capacidades do doctor passam com a conta real.

### 3.4 PRs pendentes
- PRs #31 e #33: revise contra este guia e contra a auditoria. Faça merge (seguindo a regra de §11) apenas se o CI estiver verde e eles não conflitarem com as decisões daqui. Caso contrário, explique ao Eike o que precisa mudar.

---

## 4. Arquitetura e stack

| Tema | Decisão |
|---|---|
| Linguagem e libs | Python + SDK MCP oficial + httpx (manter) |
| Execução | Servidor MCP local (stdio) no Claude Desktop |
| Instalação | Local com `uv`, em um comando. **Sem Docker**: no Mac o Docker roda numa VM Linux, onde não há AppleScript (estatísticas do app Música), Keychain nem launchd. Docker só volta se o projeto migrar para a nuvem |
| Persistência | **SQLite** no disco do Mac. Acesso isolado numa camada de repositório, para trocar por Postgres no futuro sem reescrever o resto. Supabase gratuito foi descartado: pausa após 7 dias sem uso e não tem backup automático |
| Agendamento | **launchd** do macOS (roda tarefas perdidas quando o Mac acorda [VERIFICAR comportamento com Mac desligado vs. dormindo]). Sem fila |
| Estatísticas locais | Reproduções e skips via AppleScript do app Música. Como tudo roda no Mac, sempre disponíveis |
| Camadas | Separar: cliente da API Apple / regras de playlist / persistência / ferramentas MCP. O cliente da API deve ser trocável (oficial vs. não oficial; Spotify no futuro) |
| Instruções da skill | Manter as instruções de comportamento acessíveis pelo próprio servidor MCP (ex.: prompt ou recurso MCP), para funcionarem igual no Mac e via Dispatch [VERIFICAR como o Claude Desktop/Dispatch expõe isso] |

### 4.1 Limites, retry e cache
| Item | Valor inicial [PADRÃO, configurável] |
|---|---|
| Taxa máxima | ~5 requisições/s, 2 a 3 simultâneas |
| Retry | 3 tentativas, espera exponencial 1s → 2s → 4s; respeitar `Retry-After` quando presente; 429 recua mais |
| Cache: buscas no catálogo | 7 dias |
| Cache: BPM | permanente |
| Cache: biblioteca, curtidas | 24 h |
| Snapshot de reproduções/skips | diário |

Ajustar os limites com base nos logs. Retry nunca deve travar uma chamada de ferramenta por tempo suficiente para estourar o timeout do cliente.

---

## 5. Credenciais e configuração

- **Token do Apple Music:** guardado no **Keychain do macOS** (cofre nativo do sistema, nada a instalar), via biblioteca `keyring` [VERIFICAR API atual da lib]. Alternativa: variável de ambiente, para quem preferir.
- **Configuração não secreta** em `.env` (fora do Git), com `.env.example` versionado: região da loja, chave do GetSongBPM, tópico do ntfy, URL do healthcheck, caminho do backup, fuso horário, padrões de §7.
- Cada instalação é de um usuário. Quem clona roda `login` e o token vai para o próprio Keychain.
- **Expiração do token:** detectar validade [VERIFICAR como obter], avisar pelo ntfy alguns dias antes e quando expirar. Renovação no MVP: `login` no Mac. Renovação pelo celular fica para depois.
- Nunca registrar tokens em logs, erros ou no banco em texto puro.

---

## 6. Confiabilidade e monitoramento

1. **Sinal de vida externo (dead man's switch):** um agente launchd envia um ping a cada 5–10 min para o **healthchecks.io** (plano gratuito) [VERIFICAR limites atuais e integração com ntfy]. Se o ping parar, o healthchecks avisa por e-mail e, se possível, pelo ntfy.
   - O ping deve verificar também se o app do Claude Desktop está aberto, porque o Dispatch depende dele. Se não estiver, reportar como falha.
2. **Alertas da aplicação via ntfy** (push gratuito e open source; app no iPhone; tópico com nome aleatório, tratado como segredo):
   - doctor falhou;
   - token perto de expirar ou expirado;
   - backup falhou;
   - proposta de refresh pronta para aprovação.
3. **Doctor diário** agendado pelo launchd, com alerta em caso de falha. Substitui o teste ao vivo no CI.
4. **Modo pausa** para viagens: um comando que pausa o monitoramento (e o healthchecks [VERIFICAR suporte a pausa via API]) e retoma depois, para não gerar alarme falso com o Mac fechado.
5. **Backup diário** do SQLite: usar a API de backup do SQLite (não copiar o arquivo com o banco aberto), comprimir, salvar numa pasta do iCloud Drive (caminho configurável). Retenção: 7 diárias + 4 semanais. O iCloud é compartilhado, então não acumular além disso.
6. **Logs estruturados** em arquivo local com rotação.

---

## 7. Comportamento das playlists

### 7.1 Início de toda sessão
A primeira pergunta é sempre: **editar uma playlist existente ou criar uma nova?**
- Editar: qual playlist. Se for gerenciada, carregar finalidade, perfil e parâmetros salvos.
- Se o usuário quiser **trocar a finalidade** de uma playlist existente, tratar como redefinição completa do perfil.

### 7.2 Perguntas
- **Sem limite fixo de perguntas.** Ir lapidando o contexto até entender o que a pessoa espera: finalidade, clima, artistas, mix, duração, restrições. O objetivo é atender a expectativa real, não uma associação pré-definida.
- Modo rápido (usar padrões, sem perguntas) só quando o usuário pedir explicitamente.

### 7.3 Perfis de finalidade
Perfis atuais: set de DJ, treino, corrida, estudo, festa, chill, dormir, exploração.
**Novo perfil: foco.** Para concentração, sem distrair: ondas binaurais, trilhas sonoras de filmes, nada pesado. Avaliar sobreposição com "estudo" e propor ao Eike se fica separado ou se substitui.
**Tarefa:** apresentar ao Eike a curva de energia/BPM de cada perfil (como evolui do início ao fim) para validação.

### 7.4 Mix de conhecidas e novas (dois níveis)
- **Nível 1:** % de artistas conhecidos vs. artistas novos.
- **Nível 2:** dentro dos artistas conhecidos, % de faixas conhecidas vs. faixas que o usuário nunca ouviu desses artistas (o usuário tem maior chance de gostar de material novo de quem já conhece).
- Definições [PADRÃO, configurável]:
  - **Faixa conhecida:** curtida OU reproduzida **≥ 10 vezes** (`KNOWN_MIN_PLAYS=10`).
  - **Faixa nunca ouvida:** 0 reproduções e fora da biblioteca.
  - Faixas na biblioteca com 1–9 reproduções e sem curtida: não contam como novas nem como conhecidas; ficam fora dos dois grupos por padrão.
  - **Artista conhecido:** tem pelo menos uma faixa conhecida.
- Mix padrão se o usuário não definir: 50% artistas conhecidos; dentro deles, 30% faixas conhecidas [PADRÃO].

### 7.5 Duração
Perguntar sempre. Aceitar em tempo ou em número de faixas. Se o usuário pular: **5 horas**.

### 7.6 Preview antes de criar (padrão)
- Mostrar a proposta organizada pelas partes do set antes de criar.
- O usuário ajusta ("tira esse artista", "não gosto dessa faixa", "mais energia no meio") e o sistema refaz a proposta antes de criar qualquer coisa.
- Criar direto só se o usuário pedir.

### 7.7 Playlists gerenciadas
- Toda playlist criada ou adotada pelo sistema guarda: finalidade, perfil, parâmetros de mix, duração alvo, configuração de refresh.
- A ideia central de uso: playlists permanentes que o usuário vai lapidando ao longo do tempo, adicionando, removendo e adaptando ao gosto.

### 7.8 "Não gostei"
- Vira **sugestão de remoção**, nunca remoção automática.
- Perguntar o motivo e guardar no histórico, para melhorar recomendações futuras.
- **Nunca** banir o artista inteiro por causa de uma faixa: faixas do mesmo artista podem ter vibes bem diferentes.

### 7.9 Blacklist
- Lista de artistas ou faixas que nunca entram em nenhuma playlist.
- Só existe por pedido **explícito** ("nunca mais coloca X"). Nunca inferida de um "não gostei".
- Editável pelo chat.

### 7.10 Refresh semanal
- **Opt-in por playlist** gerenciada; o usuário escolhe quais.
- Padrão: **segunda-feira, 09:00**, fuso `America/Sao_Paulo` [PADRÃO, configurável].
- Objetivo: identificar faixas pouco ouvidas ou muito puladas e propor substituições coerentes com o perfil da playlist.
- O job agendado (código determinístico) gera uma **proposta**, avisa pelo ntfy, e **só aplica após aprovação pelo Dispatch**.
- Deixar preparado um flag `REFRESH_AUTO_APPLY=false` para no futuro aplicar sozinho.

### 7.11 Descobertas da semana
- Para cada playlist principal com o recurso ativo, gerar semanalmente uma playlist **"Descobertas da semana"** com ~30 faixas novas, coerentes com o perfil da principal.
- As faixas de que o usuário mais gostar são promovidas para a playlist principal.
- Limite de promoção: no máximo **20% do tamanho da playlist principal por semana**, com teto absoluto de 30 faixas.
- Critério de "gostou" [PADRÃO, propor ao Eike para validar]: curtida, ou reproduzida várias vezes sem skip.
- Promoções também passam por aprovação, como o refresh.

### 7.12 Outros
- **Conteúdo explícito:** sem filtro. Não perguntar.
- **Nome e descrição das playlists:** em inglês.
- **Capa:** automática da Apple.
- **BPM:** GetSongBPM com chave gratuita (o Eike cria e coloca no `.env`).

---

## 8. Dados e histórico

### 8.1 Histórico completo
Registrar cada sessão: o pedido, as perguntas e respostas, as faixas propostas no preview, o que foi rejeitado e **o motivo**, as faixas finais, e cada mudança feita por refresh ou promoção. Uso: não repetir sugestões, aprender o gosto do usuário, auditar o que o sistema fez. É também a camada de dados brutos para um possível case de engenharia de dados no futuro.

### 8.2 Snapshots de estatísticas desde o início
Coletar **diariamente** reproduções e skips por faixa (AppleScript) desde a primeira versão, mesmo antes do painel existir. Sem isso, o painel e o refresh não têm histórico de tendência.

### 8.3 Modelo de dados [GATE]
Desenhar o **ERD em Mermaid** e apresentar ao Eike **antes de criar as tabelas**. Entidades sugeridas (ponto de partida, não definitivo):
- configurações do usuário;
- playlists gerenciadas (finalidade, perfil, mix, duração, refresh, descobertas);
- sessões (pedido, perguntas e respostas);
- propostas e itens propostos (aceito/rejeitado + motivo);
- eventos de faixa em playlist (adicionada/removida, origem, motivo);
- feedback de faixa ("não gostei" + motivo);
- blacklist;
- snapshots diários de estatísticas (reproduções, skips);
- cache de metadados de faixas (gênero, duração, artista) e cache de BPM;
- propostas de refresh e lotes de descobertas (pendente/aprovado/aplicado);
- execuções do doctor e do backup.

Usar migrações versionadas.

---

## 9. Painel (após o MVP funcionar)

Painel local em **Streamlit**, lendo o SQLite, com abas:
- **Saúde:** último sinal de vida, último doctor, validade do token, último backup, erros recentes.
- **Uso:** playlists criadas e editadas, faixas adicionadas e removidas, sessões por período.
- **Qualidade:** taxa de skip por playlist antes e depois de cada refresh; quantas faixas promovidas das Descobertas sobreviveram.
- **Gosto:** gêneros mais ouvidos, faixas de BPM, artistas mais ouvidos, artistas descobertos que mais agradaram, tempo de reprodução por mês e tendência (subindo ou caindo).

Propor métricas adicionais ao Eike se fizerem sentido.

---

## 10. Agentes de IA

- Na conversa (Claude Desktop / Dispatch), o próprio Claude é o agente: escolhe faixas, interpreta motivos de "não gostei", monta propostas. Custo zero além da assinatura.
- Tarefas agendadas **não chamam LLM**. Elas calculam candidatos, estatísticas e propostas de forma determinística; a decisão final acontece quando o Eike aprova pelo Dispatch.
- Tarefas autônomas com IA ficam fora do escopo enquanto exigirem API paga.

---

## 11. Qualidade e processo

- **Fluxo:** issue → branch → PR → CI.
- **Merge automático permitido** quando o CI estiver verde. O Eike não precisa revisar cada PR. Exceções que exigem o Eike: os itens [GATE] deste guia.
- **CI:** testes com respostas da API gravadas (fixtures), lint e checagem de tipos. **Sem credenciais no CI.**
- **Versionamento:** semântico, com **release-please** e commits no padrão Conventional Commits (`feat:`, `fix:`, etc.).
- **Documentação:** README em português por enquanto, com instruções de instalação para quem clonar (cenário do primo).

---

## 12. Fora de escopo agora

- Hospedagem na nuvem, autenticação pública (OAuth), multiusuário hospedado.
- Outros streamings. Quando houver, o próximo é o **Spotify**; por isso a camada de cliente deve ser trocável.
- Renovação de token pelo celular.
- Canais além do Claude/Dispatch (Telegram, atalhos do iOS).
- Case público de portfólio (pode vir depois como pipeline de dados; o histórico e os snapshots já deixam isso possível).

---

## 13. Ordem de implementação

1. **Fase 0:** auditoria, avaliação de risco, validação real, PRs #31/#33. **[GATE]**
2. **Fase 1, fundação:** configuração e credenciais (Keychain + `.env`), ERD **[GATE]** e migrações, cliente HTTP com limite/retry/cache, doctor diário, backup, snapshots diários de estatísticas, monitoramento (healthchecks + ntfy + modo pausa).
3. **Fase 2, sessão:** fluxo editar/criar, perguntas, perfis (incluindo foco e validação das curvas), mix em dois níveis, preview, playlists gerenciadas, "não gostei", blacklist, histórico.
4. **Fase 3, agendados:** refresh com aprovação, Descobertas da semana com promoção limitada.
5. **Fase 4:** painel Streamlit.

---

## 14. Pendências de verificação [VERIFICAR]

- Termos de uso da Apple e risco à conta (Fase 0).
- Validade do user token e como detectar expiração.
- Comportamento do launchd com tarefas perdidas (Mac dormindo vs. desligado).
- Limites do plano gratuito do healthchecks.io, integração com ntfy e pausa via API.
- API atual da biblioteca `keyring` com o Keychain do macOS.
- Como expor as instruções da skill pelo servidor MCP de forma que funcionem via Dispatch.
- Preço atual do Apple Developer Program (para a seção de alternativas).
- O que o AppleScript do app Música consegue fazer sozinho (alternativa de menor risco).

---

## Apêndice — Registro de decisões (pergunta → resposta)

| # | Pergunta | Decisão |
|---|---|---|
| A1 | Quem usa | Só o Eike; instalável por outros com as próprias credenciais |
| A2 | Amigos sem Claude | Fora do escopo |
| A3 | Funcionar com Mac desligado | Não no MVP; roda no Mac |
| A4 | Custo de hospedagem | Zero |
| A5 | API não oficial | Condicionada à avaliação de risco da Fase 0 |
| B1 | Onde hospedar | Mac local + Claude Dispatch |
| B2 | Agente de estatísticas no Mac | Desnecessário; tudo roda no Mac |
| B3 | Modo local | É o único modo |
| B4 | Funcionar no iPhone | Via Dispatch; instruções da skill no servidor |
| B5 | Outros canais | Só Claude/Dispatch |
| C1 | Proteção do servidor | Não se aplica (local, stdio) |
| C2 | Onde guardar o token | Keychain do macOS; config não secreta em `.env` |
| C3 | Renovação do token | Aviso por ntfy + `login` no Mac |
| C4 | Isolamento por usuário | Uma instalação por usuário |
| D1 | Perfis | 8 atuais + foco; curvas validadas pelo Eike |
| D2 | Mix | Dois níveis; conhecida = curtida ou ≥ 10 reproduções |
| D3 | Duração | Perguntar sempre; padrão 5 h |
| D4 | Nº de perguntas | Sem limite; lapidar até ter contexto |
| D5 | Rascunho | Preview como padrão, com ajustes antes de criar |
| D6 | Conteúdo explícito | Sem filtro, não perguntar |
| D7 | Blacklist | Só por pedido explícito |
| D8 | Refresh | Seg 09:00, opt-in por playlist, aplica após aprovação |
| D9 | Promoções das Descobertas | Playlist semanal de ~30; promoção ≤ 20% da principal/semana |
| D10 | "Não gostei" | Sugestão de remoção + motivo; nunca bane o artista |
| D11 | Idioma | Inglês |
| D12 | Capa | Automática |
| D13 | GetSongBPM | Sim, chave criada pelo Eike |
| D14 | Histórico | Completo |
| E1 | Persistência | SQLite + backup diário no iCloud com retenção |
| E2 | ERD | Sim, aprovado antes de implementar |
| E3 | Playlists gerenciadas | Sim, guardam finalidade e perfil |
| E4 | Agendamento | launchd |
| E5 | Limites da API | Sim (§4.1) |
| E6 | Observabilidade | Doctor diário + healthchecks + ntfy + painel |
| F1 | Stack | Python + SDK MCP + httpx |
| F2 | Deploy | Sem Docker; `uv` local; CI só testes |
| F3 | Teste ao vivo no CI | Não; vira doctor diário no Mac |
| F4 | Versionamento | SemVer + release-please |
| G1 | Outros streamings | Depois; próximo é Spotify |
| G2 | Case público | Não por enquanto |
| G3 | Fluxo de PR | Merge automático com CI verde, exceto [GATE] |
| G4 | Validação real | Fase 0, antes de tudo |
| — | Agentes de IA / RAG | Sem API paga, sem RAG; IA só na conversa |
