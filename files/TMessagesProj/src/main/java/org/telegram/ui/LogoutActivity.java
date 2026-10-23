/*
 * This is the source code of Telegram for Android v. 5.x.x.
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 *
 * Copyright Nikolai Kudashov, 2013-2018.
 */

package org.telegram.ui;

import static org.telegram.ui.Components.Premium.LimitReachedBottomSheet.TYPE_ACCOUNTS;

import android.animation.AnimatorSet;
import android.app.Dialog;
import android.content.Context;
import android.content.DialogInterface;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.TextView;

import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.DownloadController;
import org.telegram.messenger.LocaleController;
import org.telegram.messenger.MessagesController;
import org.telegram.messenger.R;
import org.telegram.messenger.SharedConfig;
import org.telegram.messenger.UserConfig;
import org.telegram.ui.ActionBar.ActionBar;
import org.telegram.ui.ActionBar.AlertDialog;
import org.telegram.ui.ActionBar.BaseFragment;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.ActionBar.ThemeDescription;
import org.telegram.ui.Cells.HeaderCell;
import org.telegram.ui.Cells.ShadowSectionCell;
import org.telegram.ui.Cells.TextDetailSettingsCell;
import org.telegram.ui.Cells.TextInfoPrivacyCell;
import org.telegram.ui.Cells.TextSettingsCell;
import org.telegram.ui.Components.AlertsCreator;
import org.telegram.ui.Components.LayoutHelper;
import org.telegram.ui.Components.Premium.LimitReachedBottomSheet;
import org.telegram.ui.Components.RecyclerListView;

import java.util.ArrayList;

public class LogoutActivity extends BaseFragment {

    private ListAdapter listAdapter;
    private RecyclerListView listView;
    private AnimatorSet animatorSet;

    private int alternativeHeaderRow;
    private int addAccountRow;
    private int passcodeRow;
    private int cacheRow;
    private int phoneRow;
    private int supportRow;
    private int alternativeSectionRow;
    private int logoutRow;
    private int logoutSectionRow;
    private int rowCount;

    @Override
    public boolean onFragmentCreate() {
        super.onFragmentCreate();

        rowCount = 0;
        alternativeHeaderRow = rowCount++;
        if (UserConfig.getActivatedAccountsCount() < UserConfig.MAX_ACCOUNT_COUNT) {
            addAccountRow = rowCount++;
        } else {
            addAccountRow = -1;
        }
        if (SharedConfig.passcodeHash.length() <= 0) {
            passcodeRow = rowCount++;
        } else {
            passcodeRow = -1;
        }
        cacheRow = rowCount++;
        phoneRow = rowCount++;
        supportRow = rowCount++;
        alternativeSectionRow = rowCount++;
        logoutRow = rowCount++;
        logoutSectionRow = rowCount++;

        return true;
    }

    @Override
    public View createView(Context context) {
        actionBar.setBackButtonImage(R.drawable.ic_ab_back);
        actionBar.setTitle(LocaleController.getString(R.string.LogOutTitle));
        if (parentLayout != null && parentLayout.isLayersLayout()) {
            actionBar.setOccupyStatusBar(false);
        }
        actionBar.setAllowOverlayTitle(true);
        actionBar.setActionBarMenuOnItemClick(new ActionBar.ActionBarMenuOnItemClick() {
            @Override
            public void onItemClick(int id) {
                if (id == -1) {
                    finishFragment();
                }
            }
        });

        listAdapter = new ListAdapter(context);

        fragmentView = new FrameLayout(context);
        fragmentView.setBackgroundColor(Theme.getColor(Theme.key_windowBackgroundGray));
        FrameLayout frameLayout = (FrameLayout) fragmentView;

        listView = new RecyclerListView(context);
        listView.setVerticalScrollBarEnabled(false);
        listView.setLayoutManager(new LinearLayoutManager(context, LinearLayoutManager.VERTICAL, false));
        frameLayout.addView(listView, LayoutHelper.createFrame(LayoutHelper.MATCH_PARENT, LayoutHelper.MATCH_PARENT, Gravity.TOP | Gravity.LEFT));
        listView.setAdapter(listAdapter);
        listView.setOnItemClickListener((view, position, x, y) -> {
            if (position == addAccountRow) {
                // Any account (custom server) may use any free slot; the premium cap
                // is enforced per-server (Telegram only) in ServerSelectFragment.
                int availableAccount = org.telegram.owpengram.OwpengramServers.firstFreeAccountSlot();
                if (availableAccount >= 0) {
                    // Xtreegram: без выбора сервера — наш сервер применяется сразу.
                    org.telegram.owpengram.OwpengramServers.applyDefaultServerForAccount(availableAccount);
                    presentFragment(new LoginActivity(availableAccount), true);
                } else {
                    LimitReachedBottomSheet limitReachedBottomSheet = new LimitReachedBottomSheet(this, getContext(), TYPE_ACCOUNTS, currentAccount, null);
                    showDialog(limitReachedBottomSheet);
                }
            } else if (position == passcodeRow) {
                presentFragment(PasscodeActivity.determineOpenFragment());
            } else if (position == cacheRow) {
                presentFragment(new CacheControlActivity());
            } else if (position == phoneRow) {
                presentFragment(new ActionIntroActivity(ActionIntroActivity.ACTION_TYPE_CHANGE_PHONE_NUMBER));
            } else if (position == supportRow) {
                showDialog(AlertsCreator.createSupportAlert(LogoutActivity.this, null));
            } else if (position == logoutRow) {
                if (getParentActivity() == null) {
                    return;
                }
                showDialog(makeLogOutDialog(getParentActivity(), currentAccount));
            }
        });
        listView.setSections();
        actionBar.setAdaptiveBackground(listView);

        return fragmentView;
    }

    public static AlertDialog makeLogOutDialog(Context context, int currentAccount) {
        AlertDialog.Builder builder = new AlertDialog.Builder(context);
        builder.setMessage(LocaleController.getString(R.string.AreYouSureLogout));
        builder.setTitle(LocaleController.getString(R.string.LogOut));
        // Xtreegram: выход из аккаунта только после ввода пароля двухэтапной
        // аутентификации (cloud password). Проверка — account.getPasswordSettings
        // с SRP-подписью: сервер отвечает ошибкой, если пароль неверный, и сам
        // вызов ничего не меняет.
        builder.setPositiveButton(LocaleController.getString(R.string.LogOut), (dialogInterface, i) -> requestLogoutWithPassword(context, currentAccount));
        builder.setNegativeButton(LocaleController.getString(R.string.Cancel), null);
        AlertDialog alertDialog = builder.create();
        TextView button = (TextView) alertDialog.getButton(DialogInterface.BUTTON_POSITIVE);
        if (button != null) {
            button.setTextColor(Theme.getColor(Theme.key_text_RedBold));
        }
        return alertDialog;
    }

    // ---- Xtreegram: выход по паролю двухэтапной аутентификации -------------

    private static void xtreeToast(Context context, String text) {
        android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_SHORT).show();
    }

    public static void requestLogoutWithPassword(Context context, int currentAccount) {
        org.telegram.tgnet.tl.TL_account.getPassword req = new org.telegram.tgnet.tl.TL_account.getPassword();
        org.telegram.tgnet.ConnectionsManager.getInstance(currentAccount).sendRequest(req, (response, error) -> AndroidUtilities.runOnUIThread(() -> {
            if (!(response instanceof org.telegram.tgnet.tl.TL_account.Password)) {
                xtreeToast(context, "Не удалось проверить пароль");
                return;
            }
            org.telegram.tgnet.tl.TL_account.Password password = (org.telegram.tgnet.tl.TL_account.Password) response;
            if (!password.has_password || password.current_algo == null) {
                // Пароль двухэтапной аутентификации не задан — проверять нечего.
                MessagesController.getInstance(currentAccount).performLogout(1);
                return;
            }
            xtreeShowPasswordDialog(context, currentAccount, password);
        }), org.telegram.tgnet.ConnectionsManager.RequestFlagWithoutLogin);
    }

    private static void xtreeShowPasswordDialog(Context context, int currentAccount, org.telegram.tgnet.tl.TL_account.Password password) {
        final android.widget.EditText editText = new android.widget.EditText(context);
        editText.setSingleLine(true);
        editText.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        editText.setHint("Пароль");
        if (password.hint != null && !password.hint.isEmpty()) {
            editText.setHint(password.hint);
        }
        AlertDialog.Builder builder = new AlertDialog.Builder(context);
        builder.setTitle(LocaleController.getString(R.string.LogOut));
        builder.setMessage("Введите пароль двухэтапной аутентификации");
        builder.setView(editText);
        builder.setPositiveButton(LocaleController.getString(R.string.OK), (dialog, which) -> {
            String value = editText.getText() == null ? "" : editText.getText().toString();
            xtreeVerifyAndLogout(context, currentAccount, password, value);
        });
        builder.setNegativeButton(LocaleController.getString(R.string.Cancel), null);
        builder.create().show();
    }

    private static void xtreeVerifyAndLogout(Context context, int currentAccount, org.telegram.tgnet.tl.TL_account.Password password, String value) {
        if (!(password.current_algo instanceof org.telegram.tgnet.TLRPC.TL_passwordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow)) {
            xtreeToast(context, "Не удалось проверить пароль");
            return;
        }
        final org.telegram.tgnet.TLRPC.TL_passwordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow algo =
                (org.telegram.tgnet.TLRPC.TL_passwordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow) password.current_algo;
        final byte[] passwordBytes = AndroidUtilities.getStringBytes(value);
        // SRP считается тяжело — уносим в фоновый поток, как в PassportActivity.
        org.telegram.messenger.Utilities.globalQueue.postRunnable(() -> {
            final byte[] xBytes = org.telegram.messenger.SRPHelper.getX(passwordBytes, algo);
            AndroidUtilities.runOnUIThread(() -> {
                org.telegram.tgnet.tl.TL_account.getPasswordSettings req = new org.telegram.tgnet.tl.TL_account.getPasswordSettings();
                req.password = org.telegram.messenger.SRPHelper.startCheck(xBytes, password.srp_id, password.srp_B, algo);
                org.telegram.tgnet.ConnectionsManager.getInstance(currentAccount).sendRequest(req, (response2, error2) -> AndroidUtilities.runOnUIThread(() -> {
                    if (error2 != null) {
                        xtreeToast(context, "Неверный пароль");
                        return;
                    }
                    MessagesController.getInstance(currentAccount).performLogout(1);
                }));
            });
        });
    }

    @Override
    protected void onDialogDismiss(Dialog dialog) {
        DownloadController.getInstance(currentAccount).checkAutodownloadSettings();
    }

    @Override
    public void onResume() {
        super.onResume();
        if (listAdapter != null) {
            listAdapter.notifyDataSetChanged();
        }
    }

    private class ListAdapter extends RecyclerListView.SelectionAdapter {

        private Context mContext;

        public ListAdapter(Context context) {
            mContext = context;
        }

        @Override
        public int getItemCount() {
            return rowCount;
        }

        @Override
        public void onBindViewHolder(RecyclerView.ViewHolder holder, int position) {
            switch (holder.getItemViewType()) {
                case 0: {
                    HeaderCell view = (HeaderCell) holder.itemView;
                    if (position == alternativeHeaderRow) {
                        view.setText(LocaleController.getString(R.string.AlternativeOptions));
                    }
                    break;
                }
                case 1: {
                    TextDetailSettingsCell view = (TextDetailSettingsCell) holder.itemView;
                    if (position == addAccountRow) {
                        view.setTextAndValueAndIcon(LocaleController.getString(R.string.AddAnotherAccount), LocaleController.getString(R.string.AddAnotherAccountInfo), R.drawable.msg_contact_add, true);
                    } else if (position == passcodeRow) {
                        view.setTextAndValueAndIcon(LocaleController.getString(R.string.SetPasscode), LocaleController.getString(R.string.SetPasscodeInfo), R.drawable.msg_permissions, true);
                    } else if (position == cacheRow) {
                        view.setTextAndValueAndIcon(LocaleController.getString(R.string.ClearCache), LocaleController.getString(R.string.ClearCacheInfo), R.drawable.msg_clearcache, true);
                    } else if (position == phoneRow) {
                        view.setTextAndValueAndIcon(LocaleController.getString(R.string.ChangePhoneNumber), LocaleController.getString(R.string.ChangePhoneNumberInfo), R.drawable.msg_newphone, true);
                    } else if (position == supportRow) {
                        view.setTextAndValueAndIcon(LocaleController.getString(R.string.ContactSupport), LocaleController.getString(R.string.ContactSupportInfo), R.drawable.msg_help, false);
                    }
                    break;
                }
                case 3: {
                    TextSettingsCell view = (TextSettingsCell) holder.itemView;
                    if (position == logoutRow) {
                        view.setTextColor(Theme.getColor(Theme.key_text_RedRegular));
                        view.setText(LocaleController.getString(R.string.LogOutTitle), false);
                    }
                    break;
                }
                case 4: {
                    TextInfoPrivacyCell view = (TextInfoPrivacyCell) holder.itemView;
                    if (position == logoutSectionRow) {
                        view.setText(LocaleController.getString(R.string.LogOutInfo));
                    }
                    break;
                }
            }
        }

        @Override
        public boolean isEnabled(RecyclerView.ViewHolder holder) {
            int position = holder.getAdapterPosition();
            return position == addAccountRow || position == passcodeRow || position == cacheRow || position == phoneRow || position == supportRow || position == logoutRow;
        }

        @Override
        public RecyclerView.ViewHolder onCreateViewHolder(ViewGroup parent, int viewType) {
            View view;
            switch (viewType) {
                case 0: {
                    view = new HeaderCell(mContext);
                    view.setBackgroundColor(Theme.getColor(Theme.key_windowBackgroundWhite));
                    break;
                }
                case 1: {
                    TextDetailSettingsCell cell = new TextDetailSettingsCell(mContext);
                    cell.setMultilineDetail(true);
                    cell.setBackgroundColor(Theme.getColor(Theme.key_windowBackgroundWhite));
                    view = cell;
                    break;
                }
                case 2: {
                    view = new ShadowSectionCell(mContext);
                    break;
                }
                case 3: {
                    view = new TextSettingsCell(mContext);
                    view.setBackgroundColor(Theme.getColor(Theme.key_windowBackgroundWhite));
                    break;
                }
                case 4:
                default: {
                    view = new TextInfoPrivacyCell(mContext);
                    view.setBackgroundDrawable(Theme.getThemedDrawableByKey(mContext, R.drawable.greydivider, Theme.key_windowBackgroundGrayShadow));
                    break;
                }
            }
            view.setLayoutParams(new RecyclerView.LayoutParams(RecyclerView.LayoutParams.MATCH_PARENT, RecyclerView.LayoutParams.WRAP_CONTENT));
            return new RecyclerListView.Holder(view);
        }

        @Override
        public int getItemViewType(int position) {
            if (position == alternativeHeaderRow) {
                return 0;
            } else if (position == addAccountRow || position == passcodeRow || position == cacheRow || position == phoneRow || position == supportRow) {
                return 1;
            } else if (position == alternativeSectionRow) {
                return 2;
            } else if (position == logoutRow) {
                return 3;
            } else {
                return 4;
            }
        }
    }

    @Override
    public ArrayList<ThemeDescription> getThemeDescriptions() {
        ArrayList<ThemeDescription> themeDescriptions = new ArrayList<>();

        themeDescriptions.add(new ThemeDescription(listView, ThemeDescription.FLAG_CELLBACKGROUNDCOLOR, new Class[]{TextSettingsCell.class, HeaderCell.class, TextDetailSettingsCell.class}, null, null, null, Theme.key_windowBackgroundWhite));
        themeDescriptions.add(new ThemeDescription(fragmentView, ThemeDescription.FLAG_BACKGROUND, null, null, null, null, Theme.key_windowBackgroundGray));

//        themeDescriptions.add(new ThemeDescription(actionBar, ThemeDescription.FLAG_BACKGROUND, null, null, null, null, Theme.key_actionBarDefault));
        themeDescriptions.add(new ThemeDescription(listView, ThemeDescription.FLAG_LISTGLOWCOLOR, null, null, null, null, Theme.key_actionBarDefault));
        themeDescriptions.add(new ThemeDescription(actionBar, ThemeDescription.FLAG_AB_ITEMSCOLOR, null, null, null, null, Theme.key_actionBarDefaultIcon));
        themeDescriptions.add(new ThemeDescription(actionBar, ThemeDescription.FLAG_AB_TITLECOLOR, null, null, null, null, Theme.key_actionBarDefaultTitle));
        themeDescriptions.add(new ThemeDescription(actionBar, ThemeDescription.FLAG_AB_SELECTORCOLOR, null, null, null, null, Theme.key_actionBarDefaultSelector));

        themeDescriptions.add(new ThemeDescription(listView, ThemeDescription.FLAG_SELECTOR, null, null, null, null, Theme.key_listSelector));

        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{View.class}, Theme.dividerPaint, null, null, Theme.key_divider));

        themeDescriptions.add(new ThemeDescription(listView, ThemeDescription.FLAG_BACKGROUNDFILTER, new Class[]{ShadowSectionCell.class}, null, null, null, Theme.key_windowBackgroundGrayShadow));

        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{TextSettingsCell.class}, new String[]{"textView"}, null, null, null, Theme.key_text_RedRegular));

        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{HeaderCell.class}, new String[]{"textView"}, null, null, null, Theme.key_windowBackgroundWhiteBlueHeader));

        themeDescriptions.add(new ThemeDescription(listView, ThemeDescription.FLAG_BACKGROUNDFILTER, new Class[]{TextInfoPrivacyCell.class}, null, null, null, Theme.key_windowBackgroundGrayShadow));
        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{TextInfoPrivacyCell.class}, new String[]{"textView"}, null, null, null, Theme.key_windowBackgroundWhiteGrayText4));

        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{TextDetailSettingsCell.class}, new String[]{"textView"}, null, null, null, Theme.key_windowBackgroundWhiteBlackText));
        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{TextDetailSettingsCell.class}, new String[]{"valueTextView"}, null, null, null, Theme.key_windowBackgroundWhiteGrayText2));
        themeDescriptions.add(new ThemeDescription(listView, 0, new Class[]{TextDetailSettingsCell.class}, new String[]{"imageView"}, null, null, null, Theme.key_windowBackgroundWhiteGrayIcon));

        return themeDescriptions;
    }
}
