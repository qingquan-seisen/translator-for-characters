【注意】
1.使用本程序需要安装Python，推荐Python3.11（安装时需要勾选Add to path，或安装后手动添加path）
2.本程序适配于能使用OpenAI接口的大语言模型，如DeepSeek、千问等
3.本程序的路径内不能含有中文，否则有可能会报错

【配置流程】
1.双击 install.bat 一键安装虚拟环境和依赖
2.填入中文的世界观描述至 world_setting.txt
3.填入术语至 terms.csv，格式为 chinese,japanese
4.填入角色风格至 characters.json
（也可略过步骤2、3、4，改用"提取知识"功能自动生成知识库。需要长文本素材，操作方法见下文。提取后会以JSON文件的形式输出至knowledge_base文件夹。此文件的优先度会低于手动输入的各个文件，比如术语表发生冲突会以terms.csv为准）

【GUI启动流程】
1.双击 start.bat 启动
2.首次启动会自动打开API配置界面，请输入对应的模型名和API KEY。

【翻译流程】
1.启动GUI
2.手动输入原文，或点击"导入原文"（支持 txt 和 csv，txt格式为【角色名】台词，csv 格式为 角色名,台词。无角色名也可，不过语气准确度会变低）
（输入例：input.txt、dialog_input.csv）
3.点击"翻译"按钮
4.等待译文输出至右侧文本框
5.译文将被保存至outputs 文件夹。手动输入或导入 txt 时保存为 txt；导入 csv 时保存为 csv。

【批量翻译流程】
1.将原文文件放置在inputs文件夹
2.双击batch_translate.bat运行
3.译文将被输出至outputs\batch文件夹

【质检流程】（包含术语正确率、参照译文相似度、AI评分和AI建议）
1.启动GUI
2.等待翻译完成，或手动输入/导入译文至右侧文本框
3.点击"质检"按钮
4.点"是"选取一份参照译文（txt 或 csv），或点"否"进入无参照模式
（参照输入例：reference.txt、dialog_reference.csv）
5.质检报告将被保存至 outputs 文件夹

【提取知识流程】
1.把中文语料放入 chinese_csv/，日文语料放入 japanese_csv/（csv 格式；文件名要成对，如 dialog_cn_03.csv ↔ dialog_jp_03.csv，且两边行数必须一致）
2.启动GUI，点"提取知识"按钮，选择提取等级（1 级：仅世界观 / 2级： 世界观+术语 / 3 级：世界观+术语+角色）
3.结果会输出至knowledge_base/，文件名带生成时间（如knowledge_20260911_065922.json），且会将 knowledge_latest.json覆盖为最新版。 
翻译和质检时默认读取 knowledge_latest.json 

【角色风格预测】（测试版）
1.在character_descriptions.txt中填入对角色风格的简单描述
2.双击predict_character.bat运行
3.结果将输出至predicted_characters.json
4.如果对结果满意，可手动将predicted_characters.json中的内容复制粘贴到characters.json，作为翻译时的参考。

【高级配置】
1.在 app_core.py 顶部有四个温度参数，可调整各个环节AI的创意度（范围0.0-2.0，建议0.1-0.3）：
   - ANALYSIS_TEMPERATURE（文本分析环节，默认 0.1）
   - TRANSLATE_TEMPERATURE（翻译环节，默认 0.25）
   - TRANSLATE_RETRY_TEMPERATURE（报错重试后的翻译环节，默认0.1）
   - REVIEW_TEMPERATURE（质检环节，默认 0.0）
2.打开 prompts.py修改提示词（所有提示词集中在这个文件里，改完保存即可）
